"""The files backend (part 4, GH #496 wave A): journaled, custody-checked,
capped, scope-tree memory on local disk.

Every test runs against a temporary directory. Crash recovery is exercised
with named failpoints (and one real SIGKILL); deadline clamping with a lock
the test itself holds — no sleeps.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from marianne.memory import (
    BackendUnreachable,
    DeadlineExceeded,
    ErrorCode,
    LockTimeout,
    MemoryBackend,
    MemoryBackendError,
    NotFound,
    Permission,
    PermissionDenied,
    RequestContext,
    SchemaViolation,
    ScopeRefused,
)
from marianne.memory.backends.files import (
    FILES_SUBTYPES,
    FilesBackend,
    files_backend_factory,
    resolve_files_location,
)
from marianne.memory.backends.files import backend as files_backend_module

ALL = tuple(p.value for p in Permission)
EVIDENCE = {"evidence_locator": "test:evidence"}
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "memory" / "files"


def make(root: Path, scope: str = "project/demo", **over: Any) -> FilesBackend:
    loc = resolve_files_location(scope, root)
    cfg: dict[str, Any] = {"attachment_id": f"att:{scope}", "scope": scope, **loc.as_config()}
    cfg.update(over)
    return files_backend_factory(cfg)


def ctx(
    scope: str = "project/demo",
    perms: tuple[str, ...] = ALL,
    seconds: float = 10.0,
    principal: str = "person:tester",
) -> RequestContext:
    return RequestContext(
        principal=principal, scope=scope, permissions=perms, deadline_at=time.time() + seconds,
    )


def declare(b: FilesBackend, text: str, scope: str = "project/demo", **kw: Any) -> str:
    kw.setdefault("provenance", EVIDENCE)
    return b.declare(ctx(scope), text, scope, **kw)["fact_id"]


def tier_path(root: Path, scope: str = "project/demo", tier: str = "recent.md") -> Path:
    return Path(resolve_files_location(scope, root).memory_tree) / tier


def assert_subtype(err: MemoryBackendError, subtype: str) -> None:
    assert err.subtype == subtype
    assert err.subtype in FILES_SUBTYPES
    assert err.code is FILES_SUBTYPES[err.subtype]


# ---------------------------------------------------------------- factory & paths


@pytest.mark.parametrize(
    ("scope", "rel"),
    [
        ("local:default", "local/default"),
        ("project/demo", "project/demo"),
        ("workspace/shared", "workspace/shared"),
        ("organization/acme", "org/acme"),
        ("concert/c-1", "concert/c-1"),
        ("person/legion", "person/legion"),
    ],
)
def test_scope_family_trees(root: Path, scope: str, rel: str) -> None:
    loc = resolve_files_location(scope, root)
    assert Path(loc.memory_tree) == root / rel
    assert Path(loc.attachment_dir) == root / rel
    assert Path(loc.state_root) == root / "trees"


def test_factory_does_no_io_and_conforms(root: Path) -> None:
    b = make(root)
    assert isinstance(b, MemoryBackend)
    assert not root.exists(), "the factory must not create directories"


@pytest.mark.parametrize(
    "over",
    [
        {"memory_tree": None},
        {"memory_tree": "relative/tree"},
        {"scope": "nonsense"},
        {"recall_cap_bytes": 10},
        {"provenance": "off"},
        {"unexpected": 1},
        {"lock_timeout_seconds": 0},
    ],
)
def test_factory_refuses_bad_config(root: Path, over: dict[str, Any]) -> None:
    loc = resolve_files_location("project/demo", root)
    cfg: dict[str, Any] = {"attachment_id": "a", "scope": "project/demo", **loc.as_config()}
    for key, value in over.items():
        if value is None:
            cfg.pop(key)
        else:
            cfg[key] = value
    with pytest.raises(SchemaViolation) as info:
        files_backend_factory(cfg)
    assert_subtype(info.value, "config_invalid")


def test_two_scopes_cannot_alias_one_tree(root: Path) -> None:
    a = make(root, "project/alpha")
    loc = resolve_files_location("project/alpha", root)
    with pytest.raises(SchemaViolation) as info:
        files_backend_factory(
            {"attachment_id": "b", "scope": "project/beta", **loc.as_config()},
        )
    assert_subtype(info.value, "config_invalid")
    assert "conflicting_scope_alias" in info.value.detail
    # the same scope may share the tree (tree-keyed custody), and closing frees it
    same = make(root, "project/alpha")
    a.close()
    same.close()
    files_backend_factory({"attachment_id": "b", "scope": "project/beta", **loc.as_config()})


def test_no_personal_paths_in_package() -> None:
    pkg = REPO / "src" / "marianne" / "memory"
    for path in pkg.rglob("*.py"):
        assert "/home/" not in path.read_text(encoding="utf-8"), path


# ---------------------------------------------------------------- declare


def test_declare_writes_a_section_and_is_idempotent(root: Path) -> None:
    b = make(root)
    result = b.declare(ctx(), "The build uses uv.", "project/demo", provenance=EVIDENCE)
    assert result["status"] == "acknowledged"
    assert result["tier"] == "recent.md"
    assert result["provenance_receipt"]["author"] == "person:tester"
    assert result["provenance_receipt"]["evidence_locator"] == "test:evidence"
    text = tier_path(root).read_text(encoding="utf-8")
    assert "The build uses uv." in text
    again = b.declare(ctx(), "The build uses uv.", "project/demo", provenance=EVIDENCE)
    assert again["fact_id"] == result["fact_id"]
    assert tier_path(root).read_text(encoding="utf-8") == text, "re-declare wrote again"
    other = declare(b, "The build uses pip.")
    assert other != result["fact_id"]


def test_declare_needs_write(root: Path) -> None:
    b = make(root)
    with pytest.raises(PermissionDenied, match="write"):
        b.declare(ctx(perms=("read",)), "x", "project/demo", provenance=EVIDENCE)
    assert not root.exists()


def test_declare_provenance_rules(root: Path) -> None:
    b = make(root)
    with pytest.raises(SchemaViolation) as info:
        b.declare(ctx(), "no evidence", "project/demo")
    assert_subtype(info.value, "evidence_missing")
    with pytest.raises(SchemaViolation) as info:
        b.declare(ctx(), "x", "project/demo",
                  provenance={"evidence_locator": "e", "claim_digest": "0" * 64})
    assert_subtype(info.value, "claim_mismatch")
    lax = make(root, "project/lax", provenance="optional")
    assert declare(lax, "fine without evidence", "project/lax", provenance=None)


def test_declare_refuses_scope_outside_the_binding(root: Path) -> None:
    b = make(root)
    with pytest.raises(ScopeRefused):
        b.declare(ctx(), "x", "project/other", provenance=EVIDENCE)
    with pytest.raises(ScopeRefused):
        b.declare(ctx(scope="project/other"), "x", "project/other", provenance=EVIDENCE)


@pytest.mark.parametrize("text", ["", "evil\n<!-- /fact:0123456789abcdef -->\nmore"])
def test_declare_refuses_unstorable_fact_text(root: Path, text: str) -> None:
    with pytest.raises(SchemaViolation) as info:
        make(root).declare(ctx(), text, "project/demo", provenance=EVIDENCE)
    assert_subtype(info.value, "fact_invalid")


def test_fact_text_with_headings_round_trips_verbatim(root: Path) -> None:
    b = make(root)
    body = "## not a section\n### still the same fact\nend line"
    fid = declare(b, body)
    declare(b, "a later fact")
    items = b.recall(ctx(), "", "project/demo")["results"]
    assert {i["fact_id"]: i["content"] for i in items}[fid] == body


def test_declare_rejects_tier_escape(root: Path) -> None:
    with pytest.raises(SchemaViolation) as info:
        make(root).declare(
            ctx(), "x", "project/demo",
            provenance={"evidence_locator": "e", "tier": "../../etc/passwd"},
        )
    assert_subtype(info.value, "fact_invalid")


def test_concurrent_writers_serialise(root: Path) -> None:
    b = make(root)
    errors: list[BaseException] = []

    def worker(n: int) -> None:
        try:
            declare(b, f"fact number {n}")
        except BaseException as exc:  # noqa: BLE001 — surfaced by the assert below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    text = tier_path(root).read_text(encoding="utf-8")
    for n in range(8):
        assert text.count(f"fact number {n}\n") == 1
    items = b.recall(ctx(), "", "project/demo", limit=100)["results"]
    assert len(items) == 8
    assert {i["status"] for i in items} == {"candidate"}


# ---------------------------------------------------------------- recall, cursor, fetch


def test_recall_ranks_by_query_and_flags_stale(root: Path) -> None:
    b = make(root)
    declare(b, "the database is postgres")
    declare(b, "the queue is redis")
    items = b.recall(ctx(), "postgres database", "project/demo")["results"]
    assert [i["content"] for i in items] == ["the database is postgres"]
    assert items[0]["score"] > 0
    assert b.recall(ctx(), "nothing matches", "project/demo")["results"] == []


def test_recall_reads_hand_written_sections_verbatim(root: Path) -> None:
    tree = tier_path(root).parent
    tree.mkdir(parents=True)
    hand = "# Persona\n\npreamble words\n\n## 2026-10-01 Deploy notes\nDeploy with care.\n"
    (tree / "growth.md").write_text(hand, encoding="utf-8")
    items = make(root).recall(ctx(), "deploy", "project/demo")["results"]
    assert len(items) == 1
    assert items[0]["content"] == "## 2026-10-01 Deploy notes\nDeploy with care.\n"
    assert items[0]["status"] == "untracked"
    assert items[0]["tier"] == "growth.md"


def test_recall_needs_read_and_is_empty_on_a_fresh_tree(root: Path) -> None:
    b = make(root)
    with pytest.raises(PermissionDenied):
        b.recall(ctx(perms=("write",)), "", "project/demo")
    result = b.recall(ctx(perms=("read",)), "", "project/demo")
    assert result == {"results": [], "cursor": None, "oversized": [], "explaining_path": None}
    assert not root.exists(), "recall must not create anything"


def test_page_cut_and_resume_return_exactly_the_rest(root: Path) -> None:
    b = make(root, recall_cap_bytes=4096)
    texts = [f"note {n:02d} " + ("x" * 900) for n in range(12)]
    for t in texts:
        declare(b, t)
    seen: list[str] = []
    result = b.recall(ctx(), "note", "project/demo")
    pages = 0
    while True:
        pages += 1
        size = sum(len(i["content"].encode()) for i in result["results"])
        assert size <= 4096
        seen.extend(i["content"] for i in result["results"])
        cursor = result["cursor"]
        if cursor is None:
            break
        assert cursor["kind"] == "page"
        assert cursor["omitted"] == len(texts) - len(seen)
        result = b.recall(ctx(), "note", "project/demo", cursor=cursor["cursor"])
    assert pages > 1
    assert sorted(seen) == sorted(texts)
    assert len(seen) == len(set(seen))


def test_limit_cuts_a_page_too(root: Path) -> None:
    b = make(root)
    for n in range(5):
        declare(b, f"item {n}")
    first = b.recall(ctx(), "item", "project/demo", limit=2)
    assert len(first["results"]) == 2
    assert first["cursor"] is not None and first["cursor"]["omitted"] == 3


def test_cursor_is_bound_to_scope_query_and_tree(root: Path) -> None:
    b = make(root, recall_cap_bytes=1024)
    for n in range(4):
        declare(b, f"entry {n} " + "y" * 600)
    cursor = b.recall(ctx(), "entry", "project/demo")["cursor"]
    assert cursor is not None and cursor["kind"] == "page"
    token = cursor["cursor"]
    with pytest.raises(SchemaViolation) as info:
        b.recall(ctx(), "different query", "project/demo", cursor=token)
    assert_subtype(info.value, "cursor_scope_mismatch")
    other = make(root, "project/other", recall_cap_bytes=1024)
    with pytest.raises(SchemaViolation) as info:
        other.recall(ctx("project/other"), "entry", "project/other", cursor=token)
    assert_subtype(info.value, "cursor_scope_mismatch")
    tampered = token[:-3] + ("aaa" if not token.endswith("aaa") else "bbb")
    with pytest.raises(SchemaViolation) as info:
        b.recall(ctx(), "entry", "project/demo", cursor=tampered)
    assert_subtype(info.value, "cursor_invalid")
    declare(b, "entry new")
    with pytest.raises(SchemaViolation) as info:
        b.recall(ctx(), "entry", "project/demo", cursor=token)
    assert_subtype(info.value, "cursor_invalid")


def test_oversized_unit_is_reported_and_fetchable(root: Path) -> None:
    b = make(root, recall_cap_bytes=32768)
    big = "é" + ("z" * 40_000) + "ünïcode tail"
    big_id = declare(b, "huge " + big)
    small_id = declare(b, "huge but small")
    result = b.recall(ctx(), "huge", "project/demo")
    assert [i["fact_id"] for i in result["results"]] == [small_id]
    assert result["cursor"] is None
    [marker] = result["oversized"]
    unit_bytes = len(("huge " + big).encode())
    assert marker == {
        "kind": "oversized_unit", "unit_id": big_id, "unit_bytes": unit_bytes,
        "cap_bytes": 32768, "remedy": "fetch",
    }
    parts: list[str] = []
    offset = 0
    while offset < unit_bytes:
        chunk = b.fetch(ctx(), big_id, offset, 7)
        assert chunk["unit_bytes"] == unit_bytes
        assert 0 < chunk["length"] <= 7
        parts.append(chunk["bytes"])
        offset += chunk["length"]
    assert "".join(parts) == "huge " + big
    # a large chunk is clamped to the page cap
    assert b.fetch(ctx(), big_id, 0, 10**9)["length"] <= 32768


def test_fetch_refuses_forged_and_mid_character_offsets(root: Path) -> None:
    b = make(root)
    fid = declare(b, "éé")
    with pytest.raises(NotFound) as info:
        b.fetch(ctx(), "f" * 16, 0, 10)
    assert_subtype(info.value, "fact_not_found")
    with pytest.raises(SchemaViolation) as bad:
        b.fetch(ctx(), fid, 1, 4)
    assert_subtype(bad.value, "argument_invalid")
    with pytest.raises(PermissionDenied):
        b.fetch(ctx(perms=("write",)), fid, 0, 4)


# ---------------------------------------------------------------- verify & bind


def test_verify_mints_a_receipt_and_never_writes_the_tier(root: Path) -> None:
    b = make(root)
    fid = declare(b, "verified fact")
    before = tier_path(root).read_bytes()
    out = b.verify(ctx(perms=("verify",)), fid)
    assert out["status"] == "verified"
    receipt = out["evidence_receipt"]
    assert receipt["fact_id"] == fid
    assert receipt["verifier"] == "person:tester"
    assert receipt["evidence_locator"] == "test:evidence"
    assert isinstance(out["receipt_token"], str)
    assert tier_path(root).read_bytes() == before
    assert b.verify(ctx(), fid, expected_evidence="other:place")["status"] == "evidence_mismatch"
    by_hash = b.verify(ctx(), receipt["content_hash"])
    assert by_hash["fact_id"] == fid
    with pytest.raises(NotFound):
        b.verify(ctx(), "0" * 16)
    with pytest.raises(PermissionDenied):
        b.verify(ctx(perms=("write",)), fid)


def test_verify_detects_disk_change_and_marks_stale(root: Path) -> None:
    b = make(root)
    fid = declare(b, "original wording")
    path = tier_path(root)
    path.write_text(path.read_text().replace("original", "edited"), encoding="utf-8")
    edited = path.read_bytes()
    out = b.verify(ctx(), fid)
    assert out["status"] == "disk_changed"
    assert "evidence_receipt" not in out
    assert path.read_bytes() == edited, "verify must not repair the tier"
    [item] = b.recall(ctx(), "wording", "project/demo")["results"]
    assert item["status"] == "stale"
    assert item["content"] == "edited wording", "disk wins"


def test_bind_requires_a_fresh_verification(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    b = make(root)
    fid = declare(b, "bindable fact")
    with pytest.raises(PermissionDenied) as info:
        b.bind(ctx(), fid, "deploy.target")
    assert_subtype(info.value, "custody_violation")
    assert "evidence_unverified" in info.value.detail
    token = b.verify(ctx(), fid)["receipt_token"]
    bound = b.bind(ctx(), fid, "deploy.target", evidence_receipt=token)
    assert bound["status"] == "bound"
    action = Path(resolve_files_location("project/demo", root).attachment_dir) / "actions"
    record = json.loads((action / f"{bound['action_id']}.json").read_text())
    assert record["fact_id"] == fid and record["principal"] == "person:tester"
    # no token: the ledger's live verification is used
    assert b.bind(ctx(), fid, "second.target")["status"] == "bound"
    with pytest.raises(PermissionDenied) as info:
        b.bind(ctx(), fid, "x", evidence_receipt=token[:-4] + "0000")
    assert "evidence_unverified" in info.value.detail
    with pytest.raises(PermissionDenied):
        b.bind(ctx(perms=("read", "verify")), fid, "x", evidence_receipt=token)
    with pytest.raises(NotFound):
        b.bind(ctx(), "0" * 16, "x")
    monkeypatch.setattr(files_backend_module, "RECEIPT_TTL_SECONDS", -1.0)
    expired = b.verify(ctx(), fid)["receipt_token"]
    with pytest.raises(PermissionDenied) as info:
        b.bind(ctx(), fid, "x", evidence_receipt=expired)
    assert "evidence_unverified" in info.value.detail


def test_bind_refuses_a_fact_changed_on_disk(root: Path) -> None:
    b = make(root)
    fid = declare(b, "stable words")
    token = b.verify(ctx(), fid)["receipt_token"]
    path = tier_path(root)
    path.write_text(path.read_text().replace("stable", "shifted"), encoding="utf-8")
    with pytest.raises(PermissionDenied) as info:
        b.bind(ctx(), fid, "x", evidence_receipt=token)
    assert_subtype(info.value, "custody_violation")
    assert "stale_fact_cannot_bind" in info.value.detail


# ---------------------------------------------------------------- forget


def test_forget_archives_and_tombstones(root: Path) -> None:
    b = make(root)
    keep = declare(b, "keep me")
    gone = declare(b, "forget me")
    with pytest.raises(PermissionDenied):
        b.forget(ctx(perms=("write",)), gone)
    out = b.forget(ctx(), gone, reason="wrong")
    assert out["status"] == "forgotten"
    assert "forget me" not in tier_path(root).read_text(encoding="utf-8")
    archive = Path(resolve_files_location("project/demo", root).attachment_dir) / "archive"
    assert any("forget me" in p.read_text() for p in archive.iterdir())
    assert [i["fact_id"] for i in b.recall(ctx(), "", "project/demo")["results"]] == [keep]
    with pytest.raises(NotFound):
        b.verify(ctx(), gone)
    with pytest.raises(NotFound):
        b.forget(ctx(), gone)
    # a deliberate re-declaration after erasure survives as a new incarnation
    assert declare(b, "forget me") == gone
    assert "forget me" in tier_path(root).read_text(encoding="utf-8")


# ---------------------------------------------------------------- custody & journal


def test_foreign_write_refuses_then_reconcile_adopts(root: Path) -> None:
    b = make(root)
    declare(b, "first")
    path = tier_path(root)
    path.write_text(path.read_text() + "\n## raw writer\nhello\n", encoding="utf-8")
    foreign = path.read_bytes()
    with pytest.raises(PermissionDenied) as info:
        declare(b, "second")
    assert_subtype(info.value, "custody_violation")
    assert "reconcile" in info.value.remedy
    assert path.read_bytes() == foreign, "raw writer bytes must survive"
    with pytest.raises(PermissionDenied):
        b.reconcile(ctx(perms=("write",)))
    b.reconcile(ctx())
    declare(b, "second")
    text = path.read_text(encoding="utf-8")
    assert "hello" in text and "second" in text


class _Crash(BaseException):
    pass


def _crash_at(point: str) -> Any:
    def hook(name: str) -> None:
        if name == point:
            raise _Crash(name)
    return hook


def _journal(root: Path) -> list[dict[str, Any]]:
    out = []
    for p in sorted((root / "trees").glob("*/journal/*.json")):
        out.append(json.loads(p.read_text()))
    return out


def test_crash_after_exchange_is_completed_by_the_next_writer(root: Path) -> None:
    b = make(root)
    b._failpoint = _crash_at("after_exchange")
    with pytest.raises(_Crash):
        declare(b, "half written")
    assert [j["status"] for j in _journal(root)] == ["pending"]
    fresh = make(root)
    fid = declare(fresh, "half written")  # retry: idempotent after recovery
    assert tier_path(root).read_text().count("half written\n") == 1
    declare(fresh, "next fact")
    assert [j["status"] for j in _journal(root)] == ["committed"] * 2, "committed kept"
    assert fresh.verify(ctx(), fid)["status"] == "verified"


def test_crash_before_exchange_is_discarded(root: Path) -> None:
    b = make(root)
    declare(b, "existing")
    before = tier_path(root).read_bytes()
    b._failpoint = _crash_at("before_exchange")
    with pytest.raises(_Crash):
        declare(b, "never landed")
    assert tier_path(root).read_bytes() == before
    fresh = make(root)
    declare(fresh, "different")
    statuses = [j["status"] for j in _journal(root)]
    assert statuses.count("aborted") == 1
    assert "never landed" not in tier_path(root).read_text()


def test_ambiguous_journal_wedges_until_resolved(root: Path) -> None:
    b = make(root)
    b._failpoint = _crash_at("after_exchange")
    with pytest.raises(_Crash):
        declare(b, "ambiguous")
    path = tier_path(root)
    path.write_text("someone else rewrote everything\n", encoding="utf-8")
    fresh = make(root)
    with pytest.raises(BackendUnreachable) as info:
        declare(fresh, "blocked")
    assert_subtype(info.value, "interrupted_adapter_write")
    assert "resolve_interrupted_journal" in info.value.remedy
    fresh.reconcile(ctx(), resolve_interrupted_journal=True)
    archived = list((root / "trees").glob("*/journal/archive/*.ambiguous.json"))
    assert len(archived) == 1
    declare(fresh, "unblocked")


def test_real_sigkill_after_exchange_recovers(root: Path) -> None:
    script = f"""
import os, signal, sys, time
sys.path.insert(0, {str(REPO / "src")!r})
from pathlib import Path
from marianne.memory import RequestContext, Permission
from marianne.memory.backends.files import files_backend_factory, resolve_files_location
loc = resolve_files_location("project/demo", Path({str(root)!r}))
b = files_backend_factory({{"attachment_id": "a", "scope": "project/demo", **loc.as_config()}})
def hook(name):
    if name == "after_exchange":
        os.kill(os.getpid(), signal.SIGKILL)
b._failpoint = hook
ctx = RequestContext(principal="person:tester", scope="project/demo",
    permissions=tuple(p.value for p in Permission), deadline_at=time.time() + 10)
b.declare(ctx, "killed mid write", "project/demo", provenance={{"evidence_locator": "e"}})
"""
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, timeout=60)
    assert proc.returncode == -9, proc.stderr.decode()[-2000:]
    b = make(root)
    declare(b, "after the kill")
    text = tier_path(root).read_text()
    assert text.count("killed mid write\n") == 1
    assert [j["status"] for j in _journal(root)] == ["committed", "committed"]


# ---------------------------------------------------------------- deadlines


def _hold_lock(root: Path, scope: str = "project/demo") -> Iterator[int]:
    b = make(root, scope)
    declare(b, "seed", scope)  # creates the state dir and lock file
    [lock] = list((root / "trees").glob("*/locks/write.lock"))
    fd = os.open(lock, os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        yield fd
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_lock_wait_is_clamped_to_the_call_deadline(root: Path) -> None:
    holder = _hold_lock(root)
    next(holder)
    try:
        before = tier_path(root).read_bytes()
        journal_before = _journal(root)
        start = time.monotonic()
        b = make(root)
        with pytest.raises(LockTimeout) as info:
            b.declare(ctx(seconds=0.4), "blocked", "project/demo", provenance=EVIDENCE)
        elapsed = time.monotonic() - start
        assert info.value.code is ErrorCode.E9
        assert elapsed < 3.0
        assert tier_path(root).read_bytes() == before
        assert _journal(root) == journal_before
    finally:
        holder.close()


def test_expired_or_nearly_expired_deadline_aborts_before_mutation(root: Path) -> None:
    b = make(root)
    with pytest.raises(DeadlineExceeded):
        b.declare(ctx(seconds=-1), "late", "project/demo", provenance=EVIDENCE)
    assert not root.exists()
    declare(b, "seed")
    before = tier_path(root).read_bytes()
    with pytest.raises((DeadlineExceeded, LockTimeout)):
        b.declare(ctx(seconds=0.05), "too close", "project/demo", provenance=EVIDENCE)
    assert tier_path(root).read_bytes() == before


# ---------------------------------------------------------------- stream_facts


def test_stream_facts_exports_facts_and_erasures_with_watermark(root: Path) -> None:
    b = make(root)
    ids = [declare(b, f"fact {n}") for n in range(10)]
    b.forget(ctx(), ids[0])
    b.forget(ctx(), ids[1])
    with pytest.raises(PermissionDenied):
        list(b.stream_facts(ctx(perms=("read",)), "project/demo"))
    batches = list(b.stream_facts(ctx(perms=("export",)), "project/demo"))
    assert batches[-1]["is_final"] and all(not x["is_final"] for x in batches[:-1])
    facts = [f for x in batches for f in x["facts"]]
    erasures = [e for x in batches for e in x["erasures"]]
    assert {f["fact_id"] for f in facts} == set(ids[2:])
    for f in facts:
        assert f["author"] == "person:tester"
        assert f["scope"] == "project/demo"
        assert len(f["content_hash"]) == 64 and f["evidence_locator"] == "test:evidence"
        assert f["revision"] >= 1 and f["incarnation"] == 1
    assert {e["fact_id"] for e in erasures} == set(ids[:2])
    assert all(e["target"] == "claim" and e["erased_by"] == "person:tester" for e in erasures)
    mark = batches[-1]["source_revision"]
    newer = declare(b, "after the mark")
    b.forget(ctx(), ids[5])
    delta = list(b.stream_facts(ctx(), "project/demo", since_revision=mark))
    assert [f["fact_id"] for x in delta for f in x["facts"]] == [newer]
    assert [e["fact_id"] for x in delta for e in x["erasures"]] == [ids[5]]


def test_stream_facts_includes_hand_written_sections(root: Path) -> None:
    tree = tier_path(root).parent
    tree.mkdir(parents=True)
    (tree / "growth.md").write_text("## Lesson\nalways test\n", encoding="utf-8")
    facts = [f for x in make(root).stream_facts(ctx(), "project/demo") for f in x["facts"]]
    assert [f["content"] for f in facts] == ["## Lesson\nalways test\n"]
    assert facts[0]["tier"] == "growth.md" and facts[0]["revision"] == 0


def test_migration_ingest_is_typed_unsupported(root: Path) -> None:
    with pytest.raises(SchemaViolation) as info:
        make(root).migration_ingest(ctx(), "m1", {"phase": "begin"})
    assert_subtype(info.value, "capability_unsupported")


# ---------------------------------------------------------------- health


def test_health_is_memory_health_v1_and_creates_nothing(root: Path) -> None:
    b = make(root)
    h = b.health()
    assert h["schema"] == "memory-health/1" and h["status"] == "healthy"
    assert not root.exists()


def test_health_degraded_while_locked(root: Path) -> None:
    holder = _hold_lock(root)
    next(holder)
    try:
        assert make(root).health()["status"] == "degraded"
    finally:
        holder.close()


def test_health_unreachable_when_tree_cannot_exist(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where a directory must go")
    h = make(blocker / "files").health()
    assert h["status"] == "unreachable" and h["failure_class"] == "unreachable"


# ---------------------------------------------------------------- noninterference


def test_scope_b_mutations_do_not_change_scope_a_recall(
    root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # SCI-6: with the clock held constant, A's recall (scores included) is identical
    monkeypatch.setattr(files_backend_module, "_now", lambda: 1_800_000_000.0)
    a = make(root, "project/alpha")
    bb = make(root, "project/beta")
    declare(a, "alpha knows this", "project/alpha")
    before = a.recall(ctx("project/alpha"), "knows", "project/alpha")
    fid = declare(bb, "beta knows this", "project/beta")
    bb.forget(ctx("project/beta"), fid)
    declare(bb, "beta again", "project/beta")
    assert a.recall(ctx("project/alpha"), "knows", "project/alpha") == before
    with pytest.raises(NotFound):
        a.verify(ctx("project/alpha"), fid)


def test_files_subtypes_are_a_closed_mapping() -> None:
    assert set(FILES_SUBTYPES) == {
        "config_invalid", "scope_unknown", "custody_violation", "preimage_mismatch",
        "interrupted_adapter_write", "fact_not_found", "evidence_missing", "claim_mismatch",
        "evidence_unavailable", "capability_unsupported", "cursor_scope_mismatch",
        "permission_denied", "lock_timeout", "deadline_exceeded",
        # wave-A additions, each named in the module docstring
        "cursor_invalid", "fact_invalid", "state_corrupt", "argument_invalid",
        "storage_error",
    }
    assert FILES_SUBTYPES["fact_not_found"] is ErrorCode.E11
    assert FILES_SUBTYPES["lock_timeout"] is ErrorCode.E9
    assert FILES_SUBTYPES["deadline_exceeded"] is ErrorCode.E10
