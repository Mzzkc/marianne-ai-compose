"""Slice 3 (gateway wave): the six-tool surface over the files backend and a fake."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from marianne.memory import (
    DeadlineExceeded,
    PermissionDenied,
    RecallCapExceeded,
    RequestContext,
    SchemaViolation,
    ScopeRefused,
    parse_access,
)
from marianne.memory.access import GrantStore
from marianne.memory.backends.files import files_backend_factory, resolve_files_location
from marianne.memory.gateway import Gateway, MemoryCaps, SessionState

P = "agent:musician-1"
SCOPE = "project/demo"
EV = {"evidence_locator": "test:evidence"}
ALL = ("read", "write", "verify", "bind", "forget")


def files_backend(root: Path, **over: Any) -> Any:
    loc = resolve_files_location(SCOPE, root)
    cfg: dict[str, Any] = {"attachment_id": "att-1", "scope": SCOPE, **loc.as_config()}
    cfg.update(over)
    return files_backend_factory(cfg)


@pytest.fixture
def gw(tmp_path: Path) -> Gateway:
    g = Gateway(require_audit=False)
    g.register_backend("att-1", files_backend(tmp_path / "files"))
    return g


def sess(g: Gateway, perms: tuple[str, ...] = ALL, **over: Any) -> str:
    kw: dict[str, Any] = {
        "principal": P, "attachment_id": "att-1", "scope": SCOPE, "permissions": perms,
        "dispatch_id": "d1", "deadline_at": time.time() + 60,
    }
    kw.update(over)
    return g.open_session(**kw).session_id


def test_full_round_trip_on_the_real_files_backend(gw: Gateway) -> None:
    sid = sess(gw)
    d = gw.declare(sid, "the build uses ruff", provenance=EV)
    assert d["status"] == "acknowledged"
    r = gw.recall(sid, "ruff")
    assert [i["fact_id"] for i in r["results"]] == [d["fact_id"]]
    v = gw.verify(sid, d["fact_id"])
    assert v["status"] == "verified"
    b = gw.bind(sid, d["fact_id"], "target:x")  # receipt taken from the session cache
    assert b["status"] == "bound"
    f = gw.forget(sid, d["fact_id"], "obsolete")
    assert f["status"] == "forgotten"
    assert gw.sessions.get(sid).state is SessionState.ACTIVE


def test_bind_without_prior_verify_is_refused(gw: Gateway) -> None:
    sid = sess(gw)
    fid = gw.declare(sid, "x fact", provenance=EV)["fact_id"]
    with pytest.raises(PermissionDenied) as exc:
        gw.bind(sid, fid, "t")
    assert exc.value.subtype == "evidence_unverified"


def test_fetch_oversized_unit(tmp_path: Path) -> None:
    g = Gateway(require_audit=False)
    g.register_backend("att-1", files_backend(tmp_path / "files", recall_cap_bytes=1024))
    sid = sess(g)
    big = "word " * 400
    g.declare(sid, big, provenance=EV)
    r = g.recall(sid, "word")
    assert r["oversized"] and r["oversized"][0]["remedy"] == "fetch"
    uid = r["oversized"][0]["unit_id"]
    got = g.fetch(sid, uid, 0, 50)
    assert got["length"] == 50 and got["unit_bytes"] > 1024


def test_recall_cursor_pages_through_backend(tmp_path: Path) -> None:
    g = Gateway(require_audit=False)
    g.register_backend("att-1", files_backend(tmp_path / "files", recall_cap_bytes=1024))
    sid = sess(g)
    for i in range(12):
        g.declare(sid, f"alpha fact number {i} " + "pad " * 40, provenance=EV)
    first = g.recall(sid, "alpha")
    assert first["cursor"] is not None
    seen = [i["fact_id"] for i in first["results"]]
    cur = first["cursor"]["cursor"]
    while cur:
        page = g.recall(sid, "alpha", cursor=cur)
        seen += [i["fact_id"] for i in page["results"]]
        cur = page["cursor"]["cursor"] if page["cursor"] else None
    assert len(seen) == len(set(seen)) == 12


def test_permission_checked_before_backend(gw: Gateway) -> None:
    sid = sess(gw, perms=("read",))
    with pytest.raises(PermissionDenied):
        gw.declare(sid, "nope", provenance=EV)
    with pytest.raises(PermissionDenied):
        gw.forget(sid, "f")
    with pytest.raises(PermissionDenied):
        gw.verify(sid, "f")
    with pytest.raises(PermissionDenied):
        gw.bind(sid, "f", "t")


def test_scope_outside_session_refused(gw: Gateway) -> None:
    sid = sess(gw)
    with pytest.raises(ScopeRefused):
        gw.recall(sid, "q", scope="project/other")
    with pytest.raises(ScopeRefused):
        gw.declare(sid, "x", scope="junk", provenance=EV)


def test_provenance_required_cap(gw: Gateway) -> None:
    sid = sess(gw, caps=MemoryCaps(provenance="required"))
    with pytest.raises(SchemaViolation) as exc:
        gw.declare(sid, "no evidence")
    assert exc.value.subtype == "provenance_required"


def test_draining_refuses_writes_allows_reads(gw: Gateway) -> None:
    sid = sess(gw)
    gw.declare(sid, "kept fact", provenance=EV)
    gw.sessions.transition(sid, SessionState.DRAINING)
    assert gw.recall(sid, "kept")["results"]
    with pytest.raises(PermissionDenied):
        gw.declare(sid, "late", provenance=EV)


def test_closed_session_refuses(gw: Gateway) -> None:
    sid = sess(gw)
    gw.close_session(sid)
    with pytest.raises(Exception) as exc:
        gw.recall(sid, "q")
    assert exc.value.subtype == "session_closed"  # type: ignore[attr-defined]


def test_dispatch_recall_quota_e4(tmp_path: Path) -> None:
    g = Gateway(require_audit=False)
    g.register_backend("att-1", files_backend(tmp_path / "files"))
    sid = sess(g, caps=MemoryCaps(dispatch_recall_quota_bytes=150))
    g.declare(sid, "quota fact " + "pad " * 20, provenance=EV)
    g.recall(sid, "quota")  # ~90 bytes
    with pytest.raises(RecallCapExceeded):
        g.recall(sid, "quota")  # the 60 left cannot hold it


# ---------------------------------------------------------------- fake backend


class Fake:
    """Records the ctx it receives; every op returns a canned shape."""

    def __init__(self) -> None:
        self.ctxs: list[RequestContext] = []

    def _rec(self, ctx: RequestContext) -> None:
        self.ctxs.append(ctx)

    def declare(self, ctx: RequestContext, *a: Any, **k: Any) -> Any:
        self._rec(ctx)
        return {"fact_id": "f", "status": "acknowledged"}

    def recall(self, ctx: RequestContext, *a: Any, **k: Any) -> Any:
        self._rec(ctx)
        return {"results": [], "cursor": None, "oversized": [], "explaining_path": None}

    def verify(self, ctx: RequestContext, *a: Any, **k: Any) -> Any:
        self._rec(ctx)
        return {"fact_id": "f", "status": "verified"}

    def bind(self, ctx: RequestContext, *a: Any, **k: Any) -> Any:
        self._rec(ctx)
        return {"status": "bound"}

    def forget(self, ctx: RequestContext, *a: Any, **k: Any) -> Any:
        self._rec(ctx)
        return {"status": "forgotten"}

    def fetch(self, ctx: RequestContext, *a: Any, **k: Any) -> Any:
        self._rec(ctx)
        return {"unit_id": "u"}


def fake_gw() -> tuple[Gateway, Fake]:
    g, f = Gateway(require_audit=False), Fake()
    g.register_backend("att-1", f)  # type: ignore[arg-type]
    return g, f


CALLS = {
    "declare": lambda g, s: g.declare(s, "t", provenance=EV),
    "recall": lambda g, s: g.recall(s, "q"),
    "verify": lambda g, s: g.verify(s, "f"),
    "bind": lambda g, s: g.bind(s, "f", "t", evidence_receipt="tok"),
    "forget": lambda g, s: g.forget(s, "f"),
    "fetch": lambda g, s: g.fetch(s, "u", 0, 1),
}


@pytest.mark.parametrize("tool", sorted(CALLS))
def test_expired_deadline_refuses_e10_before_backend(tool: str) -> None:
    g, f = fake_gw()
    sid = sess(g, deadline_at=time.time() - 1)
    with pytest.raises(DeadlineExceeded):
        CALLS[tool](g, sid)
    assert f.ctxs == []


@pytest.mark.parametrize("tool", sorted(CALLS))
def test_backend_never_gets_more_than_session_and_call_budget(tool: str) -> None:
    g, f = fake_gw()
    sid = sess(g, deadline_at=time.time() + 5, caps=MemoryCaps(per_call_budget_seconds=2.0))
    t0 = time.time()
    CALLS[tool](g, sid)
    ctx = f.ctxs[0]
    assert ctx.deadline_at <= t0 + 2.0 + 0.5
    assert ctx.principal == P and ctx.scope == SCOPE and ctx.session_id == sid
    assert ctx.dispatch_id == "d1"
    # a shorter session deadline wins over the call budget
    g2, f2 = fake_gw()
    sid2 = sess(g2, deadline_at=time.time() + 0.8, caps=MemoryCaps(per_call_budget_seconds=9.0))
    CALLS[tool](g2, sid2)
    assert f2.ctxs[0].deadline_at <= time.time() + 0.8


def test_caller_budget_can_only_shorten() -> None:
    g, f = fake_gw()
    sid = sess(g, caps=MemoryCaps(per_call_budget_seconds=5.0))
    g.recall(sid, "q", budget_seconds=1.0)
    g.recall(sid, "q", budget_seconds=500.0)
    now = time.time()
    assert f.ctxs[0].deadline_at <= now + 1.0
    assert f.ctxs[1].deadline_at <= now + 5.0 + 0.5


def test_ctx_permissions_are_the_session_ceiling_not_caller_data() -> None:
    g, f = fake_gw()
    sid = sess(g, perms=("read",))
    g.recall(sid, "q")
    assert f.ctxs[0].permissions == ("read",)


def test_unregistered_attachment_is_refused() -> None:
    g = Gateway(require_audit=False)
    with pytest.raises(Exception) as exc:
        sess(g)
    assert exc.value.subtype == "attachment_unbound"  # type: ignore[attr-defined]


# ---------------------------------------------------------------- grants wiring


def test_grant_ref_session_open_and_per_call_revocation(tmp_path: Path) -> None:
    store = GrantStore(tmp_path / "access")
    store.create("g1", P, "project/demo", ("read", "write"))
    f = Fake()
    g = Gateway(grants=store, require_audit=False)
    g.register_backend("att-1", f)  # type: ignore[arg-type]
    s = g.open_session(
        principal=P, attachment_id="att-1", scope=SCOPE, dispatch_id="d",
        deadline_at=time.time() + 60, access=parse_access("grant:g1"),
    )
    assert s.permissions == ("read", "write")
    g.recall(s.session_id, "q")
    store.revoke("g1")
    from marianne.memory import AuthorityRevoked

    with pytest.raises(AuthorityRevoked):
        g.recall(s.session_id, "q")
    assert g.sessions.get(s.session_id).state is SessionState.REVOKED
    assert len(f.ctxs) == 1


def test_grant_ref_must_match_principal_and_scope(tmp_path: Path) -> None:
    store = GrantStore(tmp_path / "access")
    store.create("g1", "agent:someone-else", "project/demo", ("read",))
    g = Gateway(grants=store, require_audit=False)
    g.register_backend("att-1", Fake())  # type: ignore[arg-type]
    with pytest.raises(ScopeRefused):
        g.open_session(
            principal=P, attachment_id="att-1", scope=SCOPE, dispatch_id="d",
            deadline_at=time.time() + 60, access=parse_access("grant:g1"),
        )


# ---------------------------------------------------------------- unexpected faults


class Boom(Fake):
    def __init__(self, exc: BaseException) -> None:
        super().__init__()
        self.exc = exc

    def recall(self, ctx: RequestContext, *a: Any, **k: Any) -> Any:
        raise self.exc


def audited(tmp_path: Path, backend: Any) -> tuple[Gateway, str, Path]:
    from marianne.memory.gateway import AuditLog

    g = Gateway(audit=AuditLog(tmp_path / "audit"))
    g.register_backend("att-1", backend)
    s = g.open_session(
        principal=P, attachment_id="att-1", scope=SCOPE, permissions=("read",),
        dispatch_id="d1", deadline_at=time.time() + 60,
    )
    return g, s.session_id, tmp_path / "audit" / "d1" / f"{s.session_id}.jsonl"


def test_unexpected_exception_is_typed_e5_audited_without_message(tmp_path: Path) -> None:
    import json

    from marianne.memory import BackendUnreachable

    secret = ValueError("SECRET memory content")
    g, sid, path = audited(tmp_path, Boom(secret))
    with pytest.raises(BackendUnreachable) as exc:
        g.recall(sid, "q")
    assert exc.value.subtype == "backend_fault"
    assert exc.value.__cause__ is secret
    assert "SECRET" not in str(exc.value)
    text = path.read_text()
    assert "SECRET" not in text
    rec = json.loads(text.splitlines()[-1])
    assert rec["result_status"] == "error" and rec["error_code"] == "E5"
    assert rec["exception_type"] == "ValueError"


@pytest.mark.parametrize("exc_type", [KeyboardInterrupt, SystemExit, asyncio.CancelledError])
def test_cancellation_is_audited_and_reraised_unchanged(tmp_path: Path, exc_type: Any) -> None:
    import json

    inst = exc_type()
    g, sid, path = audited(tmp_path, Boom(inst))
    with pytest.raises(exc_type) as exc:
        g.recall(sid, "q")
    assert exc.value is inst
    rec = json.loads(path.read_text().splitlines()[-1])
    assert rec["result_status"] == "cancelled" and rec["error_code"] is None


# ---------------------------------------------------------------- quota


def test_n_calls_never_return_more_than_the_quota(tmp_path: Path) -> None:
    g = Gateway(require_audit=False)
    g.register_backend("att-1", files_backend(tmp_path / "files", recall_cap_bytes=1024))
    sid = sess(g, caps=MemoryCaps(dispatch_recall_quota_bytes=1500))
    for i in range(10):
        g.declare(sid, f"quota fact {i} " + "pad " * 50, provenance=EV)
    total = 0
    refused = False
    for _ in range(6):
        try:
            r = g.recall(sid, "quota")
        except RecallCapExceeded:
            refused = True
            break
        total += sum(len(i["content"].encode()) for i in r["results"])
    assert total <= 1500
    assert refused


def test_zero_remaining_quota_refuses_before_backend() -> None:
    g, f = fake_gw()
    sid = sess(g, caps=MemoryCaps(dispatch_recall_quota_bytes=10))
    g.sessions.get(sid).recall_bytes_used = 10
    with pytest.raises(RecallCapExceeded):
        g.recall(sid, "q")
    assert f.ctxs == []
