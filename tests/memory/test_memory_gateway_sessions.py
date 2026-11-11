"""Slice 2 (gateway wave): the per-dispatch session table (BE2)."""

from __future__ import annotations

import time

import pytest

from marianne.memory import AuthorityRevoked, NotFound, SchemaViolation, Unauthenticated
from marianne.memory.gateway import MemoryCaps, SessionState, SessionTable

P = "agent:musician-1"


def table() -> SessionTable:
    return SessionTable()


def open_one(t: SessionTable, **over: object) -> str:
    kw: dict[str, object] = {
        "principal": P, "attachment_id": "att-1", "scope": "project/a",
        "permissions": ("read", "write"), "dispatch_id": "d1", "job_id": "j1",
        "deadline_at": time.time() + 60,
    }
    kw.update(over)
    return t.open(**kw).session_id  # type: ignore[arg-type]


def test_open_is_admitted_with_identity_caps_and_deadline() -> None:
    t = table()
    sid = open_one(t, scope="Project/A", caps=MemoryCaps(recall_cap_bytes=100))
    s = t.get(sid)
    assert s.state is SessionState.ADMITTED
    assert s.principal == P
    assert s.attachment == ("att-1", "project/a")
    assert s.caps.recall_cap_bytes == 100
    assert s.deadline_at > time.time()
    assert s.permissions == ("read", "write")


def test_ids_are_unique_and_unknown_is_not_found() -> None:
    t = table()
    assert open_one(t) != open_one(t)
    with pytest.raises(NotFound):
        t.get("nope")


@pytest.mark.parametrize(
    "over",
    [
        {"principal": "nobody"},
        {"scope": "junk"},
        {"permissions": ("fly",)},
        {"deadline_at": float("nan")},
    ],
)
def test_open_validates(over: dict[str, object]) -> None:
    with pytest.raises((SchemaViolation, Exception)) as exc:
        open_one(table(), **over)
    assert exc.value.__class__.__module__.startswith("marianne.memory")


LEGAL = {
    ("admitted", "active"), ("admitted", "draining"), ("admitted", "closed"),
    ("admitted", "revoked"), ("active", "draining"), ("active", "closed"),
    ("active", "revoked"), ("draining", "closed"), ("draining", "revoked"),
}


@pytest.mark.parametrize("src", [s.value for s in SessionState])
@pytest.mark.parametrize("dst", [s.value for s in SessionState])
def test_only_legal_transitions(src: str, dst: str) -> None:
    t = table()
    sid = open_one(t)
    s = t.get(sid)
    # drive to src by a legal path
    path = {
        "admitted": [], "active": ["active"], "draining": ["active", "draining"],
        "closed": ["closed"], "revoked": ["revoked"],
    }[src]
    for step in path:
        t.transition(sid, SessionState(step))
    assert s.state.value == src
    if (src, dst) in LEGAL:
        t.transition(sid, SessionState(dst))
        assert s.state.value == dst
    else:
        with pytest.raises(SchemaViolation) as exc:
            t.transition(sid, SessionState(dst))
        assert exc.value.subtype == "session_transition_illegal"
        assert s.state.value == src


def test_admit_call_activates_on_first_call_and_gates_by_state() -> None:
    t = table()
    sid = open_one(t)
    t.admit_call(sid, writes=True)
    assert t.get(sid).state is SessionState.ACTIVE
    t.transition(sid, SessionState.DRAINING)
    t.admit_call(sid, writes=False)  # in-flight reads still allowed
    with pytest.raises(Exception) as exc:
        t.admit_call(sid, writes=True)
    assert exc.value.subtype == "session_draining"  # type: ignore[attr-defined]
    t.transition(sid, SessionState.CLOSED)
    with pytest.raises(Unauthenticated):
        t.admit_call(sid, writes=False)


def test_revoked_session_refuses_with_e8() -> None:
    t = table()
    sid = open_one(t)
    t.revoke(sid)
    with pytest.raises(AuthorityRevoked):
        t.admit_call(sid, writes=False)


def test_close_is_idempotent_and_revoke_after_close_is_noop() -> None:
    t = table()
    sid = open_one(t)
    t.close(sid)
    t.close(sid)
    t.revoke(sid)
    assert t.get(sid).state is SessionState.CLOSED


def test_revoke_for_principal_revokes_live_sessions_only() -> None:
    t = table()
    a, b = open_one(t), open_one(t)
    c = open_one(t, principal="agent:other")
    t.close(b)
    assert t.revoke_for(principal=P) == [a]
    assert t.get(a).state is SessionState.REVOKED
    assert t.get(b).state is SessionState.CLOSED
    assert t.get(c).state is SessionState.ADMITTED
