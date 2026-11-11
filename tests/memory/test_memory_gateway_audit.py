"""Slice 4 (gateway wave): the hash-chained audit log (BE8, audit-record/1)."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import pytest

from marianne.memory import MemoryBackendError, PermissionDenied
from marianne.memory.backends.files import files_backend_factory, resolve_files_location
from marianne.memory.gateway import AuditLog, Gateway, verify_audit_file

P = "agent:musician-1"
SCOPE = "project/demo"
EV = {"evidence_locator": "test:evidence"}


def setup(tmp_path: Path, **over: Any) -> tuple[Gateway, str, Path]:
    loc = resolve_files_location(SCOPE, tmp_path / "files")
    b = files_backend_factory(
        {"attachment_id": "att-1", "scope": SCOPE, **loc.as_config()}
    )
    g = Gateway(audit=AuditLog(tmp_path / "audit"))
    g.register_backend("att-1", b)
    kw: dict[str, Any] = {
        "principal": P, "attachment_id": "att-1", "scope": SCOPE,
        "permissions": ("read", "write", "verify"), "dispatch_id": "d1",
        "deadline_at": time.time() + 60,
    }
    kw.update(over)
    s = g.open_session(**kw)
    return g, s.session_id, tmp_path / "audit" / "d1" / f"{s.session_id}.jsonl"


def lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text().splitlines()]


def test_every_call_is_one_record_and_chain_verifies(tmp_path: Path) -> None:
    g, sid, path = setup(tmp_path)
    fid = g.declare(sid, "audit me", provenance=EV)["fact_id"]
    g.recall(sid, "audit")
    g.verify(sid, fid)
    g.close_session(sid)
    recs = lines(path)
    assert [r["event"] for r in recs] == [
        "session_opened", "operation", "operation", "operation", "session_closed",
    ]
    assert [r["seq"] for r in recs] == [0, 1, 2, 3, 4]
    assert all(r["schema"] == "audit-record/1" for r in recs)
    assert recs[1]["tool"] == "declare" and recs[1]["fact_id"] == fid
    assert recs[2]["returned_fact_ids"] == [fid]
    assert recs[1]["result_status"] == "ok" and recs[1]["error_code"] is None
    assert recs[0]["prev_hash"] is None
    assert recs[1]["prev_hash"] == recs[0]["record_hash"]
    assert recs[-1]["segment_hash"] == recs[-1]["record_hash"]
    assert verify_audit_file(path).ok


def test_genesis_rule_for_first_record(tmp_path: Path) -> None:
    _, _, path = setup(tmp_path)
    r = lines(path)[0]
    body = {k: v for k, v in r.items() if k not in ("record_hash", "segment_hash")}
    canon = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    genesis = hashlib.sha256(b"GENESIS").hexdigest()
    assert r["record_hash"] == hashlib.sha256((genesis + canon).encode()).hexdigest()


def test_refusals_are_audited_with_error_code(tmp_path: Path) -> None:
    g, sid, path = setup(tmp_path, permissions=("read",))
    with pytest.raises(PermissionDenied):
        g.declare(sid, "x", provenance=EV)
    with pytest.raises(MemoryBackendError):
        g.recall(sid, "q", scope="project/other")
    recs = [r for r in lines(path) if r["event"] == "operation"]
    assert [(r["tool"], r["error_code"], r["result_status"]) for r in recs] == [
        ("declare", "E2", "refused"), ("recall", "E3", "refused"),
    ]
    assert verify_audit_file(path).ok


def test_expired_session_refusal_is_audited(tmp_path: Path) -> None:
    g, sid, path = setup(tmp_path, deadline_at=time.time() - 1)
    with pytest.raises(MemoryBackendError):
        g.recall(sid, "q")
    rec = lines(path)[-1]
    assert rec["tool"] == "recall" and rec["error_code"] == "E10"


def _mutate(path: Path, fn: Any) -> None:
    rows = path.read_text().splitlines()
    path.write_text("\n".join(fn(rows)) + "\n")


def full(tmp_path: Path) -> Path:
    g, sid, path = setup(tmp_path)
    g.declare(sid, "one", provenance=EV)
    g.recall(sid, "one")
    g.recall(sid, "one")
    g.close_session(sid)
    return path


def test_removed_middle_line_detected(tmp_path: Path) -> None:
    path = full(tmp_path)
    _mutate(path, lambda r: r[:2] + r[3:])
    res = verify_audit_file(path)
    assert not res.ok and res.first_bad_line == 3


def test_edited_line_detected(tmp_path: Path) -> None:
    path = full(tmp_path)

    def edit(rows: list[str]) -> list[str]:
        d = json.loads(rows[1])
        d["principal"] = "agent:evil"
        rows[1] = json.dumps(d, sort_keys=True)
        return rows

    _mutate(path, edit)
    res = verify_audit_file(path)
    assert not res.ok and res.first_bad_line == 2


def test_removed_first_line_detected(tmp_path: Path) -> None:
    path = full(tmp_path)
    _mutate(path, lambda r: r[1:])
    assert not verify_audit_file(path).ok


def test_removed_last_line_of_sealed_segment_detected(tmp_path: Path) -> None:
    path = full(tmp_path)
    _mutate(path, lambda r: r[:-1])
    # sealed segment loses its seal: the chain is intact but the session never closed;
    # verify(require_sealed=True) flags it.
    assert verify_audit_file(path).ok
    res = verify_audit_file(path, require_sealed=True)
    assert not res.ok


def test_edited_hash_and_reordered_lines_detected(tmp_path: Path) -> None:
    path = full(tmp_path)
    _mutate(path, lambda r: [r[0], r[2], r[1], *r[3:]])
    assert not verify_audit_file(path).ok


def test_garbage_line_detected(tmp_path: Path) -> None:
    path = full(tmp_path)
    _mutate(path, lambda r: [*r[:2], "{not json", *r[2:]])
    assert not verify_audit_file(path).ok


def test_chain_continues_across_audit_log_instances(tmp_path: Path) -> None:
    g, sid, path = setup(tmp_path)
    g.recall(sid, "q")
    g2 = Gateway(audit=AuditLog(tmp_path / "audit"))
    # a fresh AuditLog resumes from the file tail
    g2._audit.append(  # type: ignore[union-attr]
        dispatch_id="d1", session_id=sid, principal=P, event="operation", tool="recall",
        result_status="ok",
    )
    assert verify_audit_file(path).ok
    assert len(lines(path)) == 3


def test_unsafe_ids_cannot_escape_audit_root(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit")
    with pytest.raises(MemoryBackendError):
        log.append(dispatch_id="../x", session_id="s", principal=P, event="operation")
