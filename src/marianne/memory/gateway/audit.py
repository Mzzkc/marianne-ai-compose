"""Append-only, hash-chained audit log (``audit-record/1``, BE8).

One JSONL file per session at ``<root>/<dispatch_id>/<session_id>.jsonl``.
``record_hash_k = sha256(record_hash_{k-1} || canonical_json(record_k minus
record_hash/segment_hash))`` with ``record_hash_{-1} = sha256("GENESIS")``
(hex strings concatenated). The first record carries ``prev_hash = null``.
Segment sealing is limited to stamping ``segment_hash`` on the terminal
record; rotation and archival are out of scope.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from marianne.memory.errors import SchemaViolation

AUDIT_SCHEMA = "audit-record/1"
GENESIS = hashlib.sha256(b"GENESIS").hexdigest()
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_EVENTS = frozenset({"session_opened", "operation", "session_closed", "session_revoked"})
_UNHASHED = ("record_hash", "segment_hash")


def _canonical(record: dict[str, Any]) -> str:
    body = {k: v for k, v in record.items() if k not in _UNHASHED}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(prev: str, record: dict[str, Any]) -> str:
    return hashlib.sha256((prev + _canonical(record)).encode("utf-8")).hexdigest()


def _safe_id(value: str, what: str) -> str:
    if not isinstance(value, str) or not _ID_RE.match(value) or set(value) == {"."}:
        raise SchemaViolation(f"{what} {value!r} is not a safe audit path component",
                              subtype="audit_id_invalid")
    return value


@dataclass(frozen=True)
class AuditVerification:
    ok: bool
    records: int
    first_bad_line: int | None = None  # 1-based
    reason: str = ""
    sealed: bool = False


class AuditLog:
    """Writer for per-session chained segments."""

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)
        self._lock = threading.Lock()
        self._tails: dict[Path, tuple[int, str]] = {}  # path -> (next seq, last hash)

    def path_for(self, dispatch_id: str, session_id: str) -> Path:
        return (
            self._root / _safe_id(dispatch_id, "dispatch_id")
            / f"{_safe_id(session_id, 'session_id')}.jsonl"
        )

    def _tail(self, path: Path) -> tuple[int, str]:
        if path in self._tails:
            return self._tails[path]
        if not path.exists():
            return (0, GENESIS)
        rows = path.read_text(encoding="utf-8").splitlines()
        if not rows:
            return (0, GENESIS)
        last = rows[-1]
        try:
            rec = json.loads(last)
            return (int(rec["seq"]) + 1, str(rec["record_hash"]))
        except (ValueError, KeyError, TypeError):
            raise SchemaViolation(
                f"audit segment {path.name} has an unreadable tail", subtype="audit_corrupt",
            ) from None

    def append(
        self, *, dispatch_id: str, session_id: str, principal: str, event: str,
        seal: bool = False, **fields: Any,
    ) -> dict[str, Any]:
        """Append one record (fsynced) and return it. ``seal`` stamps ``segment_hash``."""
        if event not in _EVENTS:
            raise SchemaViolation(f"unknown audit event {event!r}", subtype="audit_event_invalid")
        path = self.path_for(dispatch_id, session_id)
        with self._lock:
            seq, prev = self._tail(path)
            record: dict[str, Any] = {
                "schema": AUDIT_SCHEMA, "seq": seq, "event": event, "timestamp": time.time(),
                "dispatch_id": dispatch_id, "session_id": session_id, "principal": principal,
                **fields, "prev_hash": None if seq == 0 else prev,
            }
            record["record_hash"] = _hash(prev, record)
            if seal:
                record["segment_hash"] = record["record_hash"]
            path.parent.mkdir(parents=True, exist_ok=True)
            data = (json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n").encode()
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, data)
                os.fsync(fd)
            finally:
                os.close(fd)
            self._tails[path] = (seq + 1, record["record_hash"])
            return record


def verify_audit_file(path: Path | str, *, require_sealed: bool = False) -> AuditVerification:
    """Recompute the chain; report the first line that is removed-after, edited or reordered."""
    p = Path(path)
    prev = GENESIS
    count = 0
    sealed = False
    for n, raw in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        def bad(reason: str, n: int = n, seen: int = count) -> AuditVerification:
            return AuditVerification(False, seen, n, reason)

        try:
            rec = json.loads(raw)
            if not isinstance(rec, dict):
                raise ValueError("not an object")
        except ValueError:
            return bad("line is not a JSON object")
        if rec.get("schema") != AUDIT_SCHEMA:
            return bad("wrong schema")
        if rec.get("seq") != count:
            return bad(f"seq {rec.get('seq')!r} where {count} expected (line removed or moved)")
        if rec.get("prev_hash") != (None if count == 0 else prev):
            return bad("prev_hash does not link to the previous record")
        if _hash(prev, rec) != rec.get("record_hash"):
            return bad("record_hash does not match the record (edited)")
        if sealed:
            return bad("record after the sealing record")
        if "segment_hash" in rec:
            if rec["segment_hash"] != rec["record_hash"]:
                return bad("segment_hash differs from the terminal record_hash")
            sealed = True
        prev = rec["record_hash"]
        count += 1
    if require_sealed and not sealed:
        return AuditVerification(False, count, None, "segment is not sealed", sealed=False)
    return AuditVerification(True, count, sealed=sealed)
