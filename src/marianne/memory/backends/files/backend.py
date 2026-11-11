"""The files backend: journaled, custody-checked, capped scope-tree memory on disk.

On-disk layout (all under the files root, ``~/.marianne/memory/files`` by default)::

    <root>/<family>/<id>/*.md                tier files (recent.md, growth.md, ...)
    <root>/<family>/<id>/{displaced,archive,actions}/   prior tier versions, forgotten
                                              facts, bind receipts
    <root>/trees/<tree-key>/state.json       the ledger (facts, tombstones, revision)
    <root>/trees/<tree-key>/journal/NNNNNNNN.json        write-intent journal
    <root>/trees/<tree-key>/locks/write.lock flock(2) writer lock

Every tier write is: journal ``pending`` -> displaced copy -> temp file in the
same directory -> ``os.replace`` -> ledger save -> journal ``committed``. A
crash anywhere is resolved by the next writer: tier at the post-image digest
completes the write, at the pre-image digest discards it, anything else is
ambiguous and refuses writes until ``reconcile(resolve_interrupted_journal=True)``.
The ledger remembers the digest of each tier it last wrote; a tier that differs
on disk was edited by something else (custody violation) and is refused until
``reconcile`` adopts it. A raw writer's bytes are never overwritten silently.

Known departures, wave A: no ``renameat2(RENAME_EXCHANGE)`` (plain ``os.replace``
plus a retained displaced copy), no registry persons, no ``verbatim`` legacy mode.
The writer lock is acquired by polling ``flock(LOCK_NB)`` with a short capped
backoff until the call deadline; that bounded retry is the only sleep.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import fcntl
import hashlib
import hmac
import json
import math
import os
import secrets
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from marianne.memory.backend_protocol import RequestContext
from marianne.memory.backends.files import sections
from marianne.memory.backends.files.config import FilesBackendConfig, files_error
from marianne.memory.errors import MemoryBackendError, ScopeRefused
from marianne.memory.model import (
    BindResult,
    DeclareResult,
    EvidenceReceipt,
    FetchResult,
    ForgetResult,
    MemoryHealth,
    MigrationBatch,
    MigrationEnvelope,
    MigrationResult,
    OversizedUnit,
    PageCursor,
    Permission,
    Provenance,
    RecallItem,
    RecallResult,
    VerifyResult,
    content_hash,
    derive_fact_id,
    memory_health,
    oversized_unit_cursor,
    page_cursor,
    parse_scope,
)

RECEIPT_TTL_SECONDS = 900.0
MIN_CALL_SECONDS = 0.2
BATCH_FACTS = 100
DEFAULT_TIER = "recent.md"
_TIER_NAME_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")

_TREES_LOCK = threading.Lock()
_TREES: dict[str, tuple[str, int]] = {}


def _now() -> float:
    return time.time()


def _valid_tier_name(name: str) -> bool:
    return (
        name.endswith(".md")
        and len(name) <= 128
        and name[0].isalnum()
        and set(name) <= _TIER_NAME_OK
    )


def _digest(data: bytes | None) -> str:
    return "absent" if data is None else hashlib.sha256(data).hexdigest()


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _fsync_dir(path: Path) -> None:
    with contextlib.suppress(OSError):
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _atomic_write(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` via a same-directory temp file and ``os.replace``.

    On any failure the temp file is removed and ``path`` keeps its old bytes.
    """
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:10]}.tmp")
    try:
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    _fsync_dir(path.parent)


def _read_regular(path: Path) -> bytes | None:
    """File bytes, or ``None`` when absent. Symlinks and non-files are refused."""
    try:
        st = path.lstat()
    except FileNotFoundError:
        return None
    if not path.is_file() or path.is_symlink():
        raise files_error(
            "storage_error", f"{path.name} is not a regular file",
            remedy="remove the symlink or directory occupying the tier name",
        )
    return path.read_bytes() if st.st_size >= 0 else b""


@dataclass
class _Tier:
    name: str
    data: bytes
    digest: str
    mtime: float
    units: list[sections.Unit]


@dataclass
class _Item:
    unit: sections.Unit
    record: dict[str, Any] | None
    status: str
    asserted_at: float
    evidence: str
    score: float


class FilesBackend:
    """``memory-backend/1.1`` over Markdown tier files; see the module docstring."""

    def __init__(self, config: Mapping[str, Any]) -> None:
        try:
            cfg = FilesBackendConfig.model_validate(dict(config))
        except ValidationError as exc:
            detail = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or 'config'}: {e['msg']}"
                for e in exc.errors()
            )
            raise files_error(
                "config_invalid", detail, remedy="fix the attachment configuration",
            ) from None
        if cfg.registry_root:
            raise files_error(
                "config_invalid", "registry persons are not supported in wave A",
                remedy="leave registry_root empty",
            )
        if cfg.legacy != "sectioned":
            raise files_error(
                "config_invalid", "only legacy=sectioned is built in wave A",
                remedy="use legacy: sectioned",
            )
        self._cfg = cfg
        self._scope = cfg.scope
        self._tree = Path(cfg.memory_tree)
        self._attach = Path(cfg.attachment_dir)
        key = hashlib.sha256(cfg.memory_tree.encode("utf-8")).hexdigest()[:16]
        self._state_dir = Path(cfg.state_root) / key
        self._failpoint: Callable[[str], None] | None = None
        self._closed = False
        with _TREES_LOCK:
            held = _TREES.get(cfg.memory_tree)
            if held is not None and held[0] != self._scope:
                raise files_error(
                    "config_invalid",
                    f"conflicting_scope_alias: tree {cfg.memory_tree} already serves "
                    f"{held[0]!r}, not {self._scope!r}",
                    remedy="give each scope its own tree",
                )
            _TREES[cfg.memory_tree] = (self._scope, (held[1] if held else 0) + 1)

    # ------------------------------------------------------------ lifecycle

    def close(self) -> None:
        """Release this binding's claim on its tree (idempotent)."""
        if self._closed:
            return
        self._closed = True
        with _TREES_LOCK:
            held = _TREES.get(self._cfg.memory_tree)
            if held is not None:
                if held[1] <= 1:
                    del _TREES[self._cfg.memory_tree]
                else:
                    _TREES[self._cfg.memory_tree] = (held[0], held[1] - 1)

    @contextlib.contextmanager
    def _storage(self) -> Iterator[None]:
        try:
            yield
        except OSError as exc:
            raise files_error(
                "storage_error", f"{type(exc).__name__}: {exc}",
                remedy="check permissions and free space of the memory tree",
            ) from exc

    def _fail(self, name: str) -> None:
        if self._failpoint is not None:
            self._failpoint(name)

    # ------------------------------------------------------------ guards

    def _guard(
        self, ctx: RequestContext, permission: Permission, scope: str | None = None,
    ) -> None:
        if scope is not None and parse_scope(scope).canonical != self._scope:
            raise ScopeRefused(
                f"scope {scope!r} is outside this binding ({self._scope})",
                subtype="scope_refused",
            )
        if ctx.scope != self._scope:
            raise ScopeRefused(
                f"session scope {ctx.scope!r} is outside this binding ({self._scope})",
                subtype="scope_refused",
            )
        ctx.require(permission)

    @staticmethod
    def _check_deadline(ctx: RequestContext) -> None:
        if ctx.remaining_seconds < MIN_CALL_SECONDS:
            raise files_error(
                "deadline_exceeded",
                f"{ctx.remaining_seconds:.3f}s left; a write needs at least {MIN_CALL_SECONDS}s",
                remedy="retry with a later deadline",
            )

    @contextlib.contextmanager
    def _locked(self, ctx: RequestContext, *, resolve: bool = False) -> Iterator[None]:
        """Hold the tree writer lock (deadline-bounded) and finish any interrupted write."""
        self._check_deadline(ctx)
        lock_dir = self._state_dir / "locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_dir / "write.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            end = time.monotonic() + min(self._cfg.lock_timeout_seconds, ctx.remaining_seconds)
            backoff = 0.002
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    left = end - time.monotonic()
                    if left <= 0:
                        raise files_error(
                            "lock_timeout",
                            "the tree writer lock is held by another writer",
                            remedy="retry; if it persists, find the stuck writer",
                        ) from None
                    time.sleep(min(backoff, left))  # bounded retry, not coordination
                    backoff = min(backoff * 2, 0.05)
            try:
                self._check_deadline(ctx)
                self._recover(resolve=resolve)
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    # ------------------------------------------------------------ ledger

    def _load_state(self) -> dict[str, Any]:
        path = self._state_dir / "state.json"
        data = _read_regular(path)
        if data is None:
            return {
                "schema": "files-state/1", "key": "", "revision": 0, "tiers": {},
                "facts": {}, "incarnations": {}, "tombstones": [],
            }
        try:
            state = json.loads(data)
            if not isinstance(state, dict) or state.get("schema") != "files-state/1":
                raise ValueError("unexpected shape")
        except ValueError as exc:
            raise files_error(
                "state_corrupt", f"{path.name} cannot be parsed: {exc}",
                remedy="restore state.json from backup or remove it and run reconcile",
            ) from None
        return state

    def _save_state(self, state: dict[str, Any]) -> None:
        if not state.get("key"):
            state["key"] = secrets.token_hex(32)
        self._state_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(
            self._state_dir / "state.json",
            json.dumps(state, sort_keys=True).encode("utf-8"),
        )

    @staticmethod
    def _apply_patch(state: dict[str, Any], patch: Mapping[str, Any]) -> None:
        state["revision"] = max(int(state["revision"]), int(patch["revision"]))
        for tier, dig in patch.get("tiers", {}).items():
            if dig is None:
                state["tiers"].pop(tier, None)
            else:
                state["tiers"][tier] = dig
        for fid in patch.get("facts_del", []):
            state["facts"].pop(fid, None)
        state["facts"].update(patch.get("facts_set", {}))
        state["incarnations"].update(patch.get("incarnations", {}))
        known = {t["tombstone_id"] for t in state["tombstones"]}
        for tomb in patch.get("tombstones_add", []):
            if tomb["tombstone_id"] not in known:
                state["tombstones"].append(tomb)

    # ------------------------------------------------------------ journal

    def _journal_dir(self) -> Path:
        return self._state_dir / "journal"

    def _next_seq(self) -> int:
        top = 0
        for folder in (self._journal_dir(), self._journal_dir() / "archive"):
            if folder.is_dir():
                for p in folder.iterdir():
                    head = p.name.split(".", 1)[0]
                    if head.isdigit():
                        top = max(top, int(head))
        return top + 1

    def _write_journal(self, entry: dict[str, Any]) -> None:
        self._journal_dir().mkdir(parents=True, exist_ok=True)
        _atomic_write(
            self._journal_dir() / f"{entry['seq']:08d}.json",
            json.dumps(entry, sort_keys=True).encode("utf-8"),
        )

    def _recover(self, *, resolve: bool = False) -> None:
        jdir = self._journal_dir()
        if not jdir.is_dir():
            return
        for path in sorted(jdir.glob("*.json")):
            try:
                entry = json.loads(path.read_bytes())
            except ValueError:
                raise files_error(
                    "state_corrupt", f"journal entry {path.name} cannot be parsed",
                    remedy="inspect the journal directory",
                ) from None
            if entry.get("status") != "pending":
                continue
            current = _digest(_read_regular(self._tree / entry["tier"]))
            if current == entry["post"]:
                state = self._load_state()
                self._apply_patch(state, entry["patch"])
                self._save_state(state)
                entry["status"] = "committed"
                self._write_journal(entry)
            elif current == entry["pre"]:
                entry["status"] = "aborted"
                self._write_journal(entry)
            elif resolve:
                archive = jdir / "archive"
                archive.mkdir(exist_ok=True)
                entry["status"] = "ambiguous"
                _atomic_write(
                    archive / f"{entry['seq']:08d}.ambiguous.json",
                    json.dumps(entry, sort_keys=True).encode("utf-8"),
                )
                path.unlink()
            else:
                raise files_error(
                    "interrupted_adapter_write",
                    f"journal {path.name} for {entry['tier']} matches neither its pre- "
                    "nor post-image; the tier was changed by someone else",
                    remedy="inspect the tier, then run reconcile with "
                    "resolve_interrupted_journal=True",
                )

    def _commit_tier(
        self, op: str, ctx: RequestContext, tier: str, old: bytes | None, new: bytes,
        patch: dict[str, Any],
    ) -> None:
        """Journalled, atomic replacement of one tier file plus its ledger patch."""
        seq = self._next_seq()
        patch["tiers"] = {**patch.get("tiers", {}), tier: _digest(new)}
        entry: dict[str, Any] = {
            "schema": "files-journal/1", "seq": seq, "status": "pending", "op": op,
            "tier": tier, "pre": _digest(old), "post": _digest(new),
            "principal": ctx.principal, "created_at": _now(), "patch": patch,
        }
        self._write_journal(entry)
        self._tree.mkdir(parents=True, exist_ok=True)
        if old is not None:
            displaced = self._attach / "displaced"
            displaced.mkdir(exist_ok=True)
            _atomic_write(displaced / f"{tier}.{seq:08d}.prev", old)
        self._fail("before_exchange")
        _atomic_write(self._tree / tier, new)
        self._fail("after_exchange")
        state = self._load_state()
        self._apply_patch(state, patch)
        self._save_state(state)
        entry["status"] = "committed"
        self._write_journal(entry)

    # ------------------------------------------------------------ tier scanning

    def _scan(self) -> dict[str, _Tier]:
        tiers: dict[str, _Tier] = {}
        if not self._tree.is_dir():
            return tiers
        for path in sorted(self._tree.iterdir()):
            if not _valid_tier_name(path.name) or path.is_symlink() or not path.is_file():
                continue
            data = path.read_bytes()
            tiers[path.name] = _Tier(
                path.name, data, _digest(data), path.stat().st_mtime,
                sections.parse_tier(path.name, sections.decode(data)),
            )
        return tiers

    @staticmethod
    def _find_unit(tiers: Mapping[str, _Tier], unit_id: str) -> tuple[_Tier, sections.Unit] | None:
        for tier in tiers.values():
            for unit in tier.units:
                if unit.unit_id == unit_id:
                    return tier, unit
        return None

    @staticmethod
    def _live_verification(record: Mapping[str, Any], chash: str) -> bool:
        ver = record.get("verification")
        if not ver:
            return False
        return bool(ver["content_hash"] == chash and ver["expires_at"] > _now())

    def _items(self, state: Mapping[str, Any], tiers: Mapping[str, _Tier], qt: frozenset[str],
               now: float) -> list[_Item]:
        items: list[_Item] = []
        for tier in tiers.values():
            for unit in tier.units:
                if not sections.matches(qt, unit.content):
                    continue
                record = state["facts"].get(unit.fact_id) if unit.fact_id else None
                if record is None:
                    status, asserted, evidence = "untracked", tier.mtime, ""
                else:
                    chash = content_hash(unit.content)
                    if chash != record["content_hash"]:
                        status = "stale"
                    elif self._live_verification(record, chash):
                        status = "verified"
                    else:
                        status = "candidate"
                    asserted, evidence = float(record["asserted_at"]), record["evidence_locator"]
                items.append(_Item(
                    unit, record, status, asserted, evidence,
                    sections.score(qt, unit.content, asserted, now),
                ))
        items.sort(key=lambda i: (-i.score, -i.asserted_at, i.unit.unit_id))
        return items

    def _tree_digest(self, state: Mapping[str, Any], tiers: Mapping[str, _Tier]) -> str:
        parts = [f"rev:{state['revision']}"] + [f"{t.name}:{t.digest}" for t in tiers.values()]
        return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:24]

    # ------------------------------------------------------------ declare

    def declare(
        self, ctx: RequestContext, fact: str, scope: str,
        provenance: Provenance | None = None, confidence: float = 1.0,
        tags: list[str] | None = None,
    ) -> DeclareResult:
        self._guard(ctx, Permission.WRITE, scope)
        self._check_deadline(ctx)
        text = fact
        if not isinstance(text, str) or not text.strip():
            raise files_error("fact_invalid", "fact text is empty", remedy="declare real text")
        if sections.has_marker(text):
            raise files_error(
                "fact_invalid", "fact text contains a line the tier format reserves",
                remedy="remove the `<!-- fact:... -->` marker from the text",
            )
        if "\x00" in text:
            raise files_error("fact_invalid", "fact text contains a NUL byte")
        try:
            text.encode("utf-8")
        except UnicodeEncodeError:
            raise files_error("fact_invalid", "fact text is not valid Unicode") from None
        if (
            not isinstance(confidence, (int, float)) or isinstance(confidence, bool)
            or not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0
        ):
            raise files_error("argument_invalid", "confidence must be within 0..1")
        if tags is not None and not (
            isinstance(tags, list) and all(isinstance(t, str) for t in tags)
        ):
            raise files_error("argument_invalid", "tags must be a list of strings")
        prov: Mapping[str, Any] = provenance or {}
        locator = str(prov.get("evidence_locator") or "")
        if not locator and self._cfg.provenance == "required":
            raise files_error(
                "evidence_missing", "declare needs provenance.evidence_locator",
                remedy="pass provenance={'evidence_locator': <where the claim can be re-checked>}",
            )
        chash = content_hash(text)
        claim = str(prov.get("claim_digest") or chash)
        if claim != chash:
            raise files_error(
                "claim_mismatch", "provenance.claim_digest does not match the fact text",
                remedy="omit claim_digest or pass the SHA-256 of the exact text",
            )
        tier = str(prov.get("tier") or DEFAULT_TIER)
        if not _valid_tier_name(tier):
            raise files_error(
                "fact_invalid", f"tier {tier!r} is not a plain *.md file name",
                remedy="use a name like recent.md",
            )
        fid = derive_fact_id(self._scope, locator, chash, claim)
        with self._storage(), self._locked(ctx):
            state = self._load_state()
            record = state["facts"].get(fid)
            if record is None:
                old = _read_regular(self._tree / tier)
                self._check_custody(state, tier, old)
                when = _now()
                rev = int(state["revision"]) + 1
                incarnation = int(state["incarnations"].get(fid, 0)) + 1
                record = {
                    "fact_id": fid, "content_hash": chash, "claim_digest": claim,
                    "evidence_locator": locator, "tier": tier, "author": ctx.principal,
                    "asserted_at": when, "revision": rev, "incarnation": incarnation,
                    "confidence": float(confidence), "tags": list(tags or []),
                    "verification": None,
                }
                block = sections.encode(sections.render_block(fid, text, when))
                sep = b"" if not old else (b"\n" if old.endswith(b"\n") else b"\n\n")
                self._commit_tier(
                    "declare", ctx, tier, old, (old or b"") + sep + block,
                    {"revision": rev, "facts_set": {fid: record},
                     "incarnations": {fid: incarnation}},
                )
            return {
                "fact_id": fid, "hash": record["content_hash"],
                "written_at": record["asserted_at"], "tier": record["tier"],
                "status": "acknowledged",
                "provenance_receipt": {
                    "fact_id": fid, "claim_digest": record["claim_digest"],
                    "evidence_locator": record["evidence_locator"],
                    "author": record["author"], "written_at": record["asserted_at"],
                },
            }

    @staticmethod
    def _check_custody(state: Mapping[str, Any], tier: str, current: bytes | None) -> None:
        known = state["tiers"].get(tier)
        if (known is None and current is None) or known == _digest(current):
            return
        raise files_error(
            "custody_violation",
            f"{tier} was changed outside the backend since its last write",
            remedy="run reconcile to adopt the on-disk tier, then retry",
        )

    # ------------------------------------------------------------ recall / fetch

    def _encode_cursor(self, query: str, tree: str, now: float, pos: int) -> str:
        body = _b64e(json.dumps(
            {"v": 1, "scope": self._scope, "query": query, "tree": tree, "now": now, "pos": pos},
            sort_keys=True,
        ).encode())
        return f"{body}.{hashlib.sha256(body.encode()).hexdigest()[:24]}"

    def _decode_cursor(self, token: str, query: str) -> dict[str, Any]:
        bad = files_error(
            "cursor_invalid", "the recall cursor is malformed or was altered",
            remedy="start the recall again without a cursor",
        )
        body, _, sig = token.partition(".") if isinstance(token, str) else ("", "", "")
        if not sig or not hmac.compare_digest(
            sig, hashlib.sha256(body.encode()).hexdigest()[:24],
        ):
            raise bad
        try:
            payload = json.loads(_b64d(body))
            if not isinstance(payload, dict) or payload["v"] != 1 or int(payload["pos"]) < 0:
                raise bad
        except (ValueError, KeyError, TypeError, binascii.Error):
            raise bad from None
        if payload["scope"] != self._scope or payload["query"] != query:
            raise files_error(
                "cursor_scope_mismatch", "the cursor belongs to another scope or query",
                remedy="resume with the same scope and query that produced it",
            )
        return payload

    def recall(
        self, ctx: RequestContext, query: str, scope: str, limit: int = 100,
        cursor: str | None = None,
    ) -> RecallResult:
        self._guard(ctx, Permission.READ, scope)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise files_error("argument_invalid", "limit must be a positive integer")
        payload = self._decode_cursor(cursor, query) if cursor is not None else None
        with self._storage():
            state = self._load_state()
            tiers = self._scan()
            tree = self._tree_digest(state, tiers)
            if payload is not None and payload["tree"] != tree:
                raise files_error(
                    "cursor_invalid", "the tree changed since the cursor was issued",
                    remedy="start the recall again without a cursor",
                )
            now = float(payload["now"]) if payload else _now()
            pos = int(payload["pos"]) if payload else 0
            cap = self._cfg.recall_cap_bytes
            ranked = self._items(state, tiers, sections.tokens(query), now)
            oversized: list[OversizedUnit] = []
            fits: list[_Item] = []
            for item in ranked:
                if item.unit.size > cap:
                    oversized.append(oversized_unit_cursor(item.unit.unit_id, item.unit.size, cap))
                else:
                    fits.append(item)
            if payload is not None:
                oversized = []
            page: list[RecallItem] = []
            used = 0
            index = pos
            while index < len(fits) and len(page) < limit:
                item = fits[index]
                if used + item.unit.size > cap:
                    break
                used += item.unit.size
                page.append({
                    "fact_id": item.unit.unit_id, "content": item.unit.content,
                    "score": item.score, "tier": item.unit.tier,
                    "asserted_at": item.asserted_at, "evidence_locator": item.evidence,
                    "status": item.status,
                })
                index += 1
            cut: PageCursor | None = None
            if index < len(fits):
                cut = page_cursor(
                    self._encode_cursor(query, tree, now, index),
                    fits[index].unit.unit_id, len(fits) - index,
                )
            return {"results": page, "cursor": cut, "oversized": oversized,
                    "explaining_path": None}

    def fetch(self, ctx: RequestContext, unit_id: str, offset: int, length: int) -> FetchResult:
        self._guard(ctx, Permission.READ)
        for name, val in (("offset", offset), ("length", length)):
            if not isinstance(val, int) or isinstance(val, bool):
                raise files_error("argument_invalid", f"{name} must be an integer")
        if offset < 0 or length < 1:
            raise files_error("argument_invalid", "offset must be >= 0 and length >= 1")
        with self._storage():
            found = self._find_unit(self._scan(), unit_id)
        if found is None:
            raise files_error("fact_not_found", f"no unit {unit_id!r} in this scope")
        raw = found[1].content.encode("utf-8")
        if offset > len(raw):
            raise files_error("argument_invalid", f"offset {offset} is past the end ({len(raw)})")
        if offset < len(raw) and (raw[offset] & 0xC0) == 0x80:
            raise files_error(
                "argument_invalid", f"offset {offset} is inside a UTF-8 character",
                remedy="resume at the offset you were given (offset + length)",
            )
        end = min(len(raw), offset + min(length, self._cfg.recall_cap_bytes))
        while end < len(raw) and end > offset and (raw[end] & 0xC0) == 0x80:
            end -= 1
        if end == offset and offset < len(raw):
            raise files_error("argument_invalid", "length is smaller than one character")
        return {
            "unit_id": unit_id, "bytes": raw[offset:end].decode("utf-8"),
            "unit_bytes": len(raw), "offset": offset, "length": end - offset,
        }

    # ------------------------------------------------------------ verify / bind

    def _sign(self, state: Mapping[str, Any], body: str) -> str:
        key = str(state.get("key") or "")
        if not key:
            return ""
        return hmac.new(key.encode(), body.encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def _lookup(state: Mapping[str, Any], ref: str) -> dict[str, Any] | None:
        facts: Mapping[str, dict[str, Any]] = state["facts"]
        if ref in facts:
            return facts[ref]
        for fid in sorted(facts):
            if facts[fid]["content_hash"] == ref:
                return facts[fid]
        return None

    @staticmethod
    def _disk_unit(tiers: Mapping[str, _Tier], record: Mapping[str, Any]) -> sections.Unit | None:
        tier = tiers.get(record["tier"])
        if tier is None:
            return None
        return next((u for u in tier.units if u.fact_id == record["fact_id"]), None)

    def verify(
        self, ctx: RequestContext, fact_id_or_hash: str, expected_evidence: str = "",
    ) -> VerifyResult:
        self._guard(ctx, Permission.VERIFY)
        self._check_deadline(ctx)
        with self._storage(), self._locked(ctx):
            state = self._load_state()
            record = self._lookup(state, str(fact_id_or_hash))
            if record is None:
                raise files_error("fact_not_found", f"no fact {fact_id_or_hash!r} in this scope")
            fid = record["fact_id"]
            unit = self._disk_unit(self._scan(), record)
            if unit is None or content_hash(unit.content) != record["content_hash"]:
                return {
                    "fact_id": fid, "status": "disk_changed",
                    "correction": {
                        "remedy": "the tier no longer holds the declared text; "
                        "re-declare it or run reconcile",
                    },
                }
            if expected_evidence and expected_evidence != record["evidence_locator"]:
                return {"fact_id": fid, "status": "evidence_mismatch"}
            when = _now()
            expires = when + RECEIPT_TTL_SECONDS
            body = {
                "fact_id": fid, "claim_digest": record["claim_digest"],
                "evidence_locator": record["evidence_locator"],
                "content_hash": record["content_hash"],
                "evidence_version": record["content_hash"][:16],
                "verified_at": when, "verifier": ctx.principal, "expires_at": expires,
            }
            if not state.get("key"):
                state["key"] = secrets.token_hex(32)
            payload = _b64e(json.dumps(body, sort_keys=True).encode())
            sig = self._sign(state, payload)
            record["verification"] = {
                "content_hash": record["content_hash"], "verified_at": when,
                "expires_at": expires, "verifier": ctx.principal,
            }
            self._save_state(state)
            receipt: EvidenceReceipt = {**body, "signature": sig}  # type: ignore[typeddict-item]
            return {
                "fact_id": fid, "status": "verified", "evidence_receipt": receipt,
                "receipt_token": f"{payload}.{sig}",
            }

    def _token_ok(self, state: Mapping[str, Any], token: str, record: Mapping[str, Any]) -> bool:
        payload, _, sig = token.partition(".")
        good = self._sign(state, payload)
        if not good or not hmac.compare_digest(sig, good):
            return False
        try:
            body = json.loads(_b64d(payload))
            return bool(
                body["fact_id"] == record["fact_id"]
                and body["content_hash"] == record["content_hash"]
                and float(body["expires_at"]) > _now()
            )
        except (ValueError, KeyError, TypeError, binascii.Error):
            return False

    def bind(
        self, ctx: RequestContext, fact_id: str, target_symbol: str,
        evidence_receipt: str | None = None,
    ) -> BindResult:
        self._guard(ctx, Permission.BIND)
        self._check_deadline(ctx)
        if not isinstance(target_symbol, str) or not target_symbol.strip():
            raise files_error("argument_invalid", "target_symbol must be a non-empty string")
        with self._storage(), self._locked(ctx):
            state = self._load_state()
            record = state["facts"].get(fact_id)
            if record is None:
                raise files_error("fact_not_found", f"no fact {fact_id!r} in this scope")
            unit = self._disk_unit(self._scan(), record)
            if unit is None or content_hash(unit.content) != record["content_hash"]:
                raise files_error(
                    "custody_violation",
                    "stale_fact_cannot_bind: the fact changed on disk since it was declared",
                    remedy="verify the fact again (or re-declare it) before binding",
                )
            if evidence_receipt is not None:
                fresh = self._token_ok(state, str(evidence_receipt), record)
            else:
                fresh = self._live_verification(record, record["content_hash"])
            if not fresh:
                raise files_error(
                    "custody_violation",
                    "evidence_unverified: no fresh verification receipt for this fact",
                    remedy="call verify, then bind with the receipt_token",
                )
            when = _now()
            action_id = hashlib.sha256(
                f"{fact_id}\0{target_symbol}\0{when}\0{secrets.token_hex(8)}".encode(),
            ).hexdigest()[:16]
            actions = self._attach / "actions"
            actions.mkdir(parents=True, exist_ok=True)
            _atomic_write(
                actions / f"{action_id}.json",
                json.dumps({
                    "schema": "files-action/1", "action_id": action_id, "fact_id": fact_id,
                    "target_symbol": target_symbol, "principal": ctx.principal,
                    "bound_at": when, "content_hash": record["content_hash"],
                }, sort_keys=True).encode("utf-8"),
            )
            return {
                "action_id": action_id, "fact_id": fact_id, "target_symbol": target_symbol,
                "bound_at": when, "status": "bound",
            }

    # ------------------------------------------------------------ forget

    def forget(self, ctx: RequestContext, fact_id: str, reason: str = "") -> ForgetResult:
        self._guard(ctx, Permission.FORGET)
        self._check_deadline(ctx)
        with self._storage(), self._locked(ctx):
            state = self._load_state()
            record = state["facts"].get(fact_id)
            if record is None:
                raise files_error("fact_not_found", f"no fact {fact_id!r} in this scope")
            tier_name = record["tier"]
            tiers = self._scan()
            when = _now()
            rev = int(state["revision"]) + 1
            tomb = {
                "tombstone_id": hashlib.sha256(
                    f"{fact_id}\0{record['incarnation']}\0{rev}".encode(),
                ).hexdigest()[:16],
                "fact_id": fact_id, "target": "claim", "cutoff": int(record["incarnation"]),
                "target_contribution_ids": [], "claim_digest": record["claim_digest"],
                "evidence_locator": record["evidence_locator"],
                "content_hash": record["content_hash"], "erased_at": when,
                "erased_by": ctx.principal, "reason": str(reason), "revision": rev,
            }
            patch: dict[str, Any] = {
                "revision": rev, "facts_del": [fact_id], "tombstones_add": [tomb],
            }
            unit = self._disk_unit(tiers, record)
            tier = tiers.get(tier_name)
            if unit is None or tier is None:
                state2 = state
                self._apply_patch(state2, patch)
                self._save_state(state2)
            else:
                self._check_custody(state, tier_name, tier.data)
                text = sections.decode(tier.data)
                archive = self._attach / "archive"
                archive.mkdir(parents=True, exist_ok=True)
                _atomic_write(
                    archive / f"{fact_id}.{rev:08d}.md",
                    sections.encode(text[unit.start:unit.end]),
                )
                new = sections.encode(text[:unit.start] + text[unit.end:])
                self._commit_tier("forget", ctx, tier_name, tier.data, new, patch)
            return {
                "fact_id": fact_id, "tombstone_id": tomb["tombstone_id"],
                "status": "forgotten", "erased_at": when,
            }

    # ------------------------------------------------------------ reconcile

    def reconcile(
        self, ctx: RequestContext, resolve_interrupted_journal: bool = False,
    ) -> dict[str, Any]:
        """Adopt the tiers as they are on disk (admin). Never rewrites a tier."""
        self._guard(ctx, Permission.ADMIN)
        self._check_deadline(ctx)
        with self._storage(), self._locked(ctx, resolve=bool(resolve_interrupted_journal)):
            state = self._load_state()
            tiers = self._scan()
            adopted = sorted(
                name for name, t in tiers.items() if state["tiers"].get(name) != t.digest
            )
            state["tiers"] = {name: t.digest for name, t in tiers.items()}
            self._save_state(state)
            return {"adopted": adopted, "revision": int(state["revision"])}

    # ------------------------------------------------------------ stream_facts

    def stream_facts(
        self, ctx: RequestContext, scope: str, since_revision: int | None = None,
    ) -> Iterator[MigrationBatch]:
        self._guard(ctx, Permission.EXPORT, scope)
        if since_revision is not None and (
            not isinstance(since_revision, int) or since_revision < 0
        ):
            raise files_error("argument_invalid", "since_revision must be an integer >= 0")
        with self._storage():
            state = self._load_state()
            tiers = self._scan()
        return self._batches(state, tiers, since_revision)

    def _batches(
        self, state: Mapping[str, Any], tiers: Mapping[str, _Tier], since: int | None,
    ) -> Iterator[MigrationBatch]:
        floor = since if since is not None else -1
        rev = int(state["revision"])
        facts: list[dict[str, Any]] = []
        for tier in tiers.values():
            for unit in tier.units:
                record = state["facts"].get(unit.fact_id) if unit.fact_id else None
                chash = content_hash(unit.content)
                entry: dict[str, Any]
                if record is None:
                    entry = {
                        "fact_id": unit.unit_id, "content": unit.content, "content_hash": chash,
                        "claim_digest": chash, "evidence_locator": "", "tier": tier.name,
                        "scope": self._scope, "author": "", "asserted_at": tier.mtime,
                        "revision": 0, "incarnation": 0,
                    }
                else:
                    entry = {
                        "fact_id": record["fact_id"], "content": unit.content,
                        "content_hash": chash, "claim_digest": record["claim_digest"],
                        "evidence_locator": record["evidence_locator"], "tier": tier.name,
                        "scope": self._scope, "author": record["author"],
                        "asserted_at": record["asserted_at"], "revision": record["revision"],
                        "incarnation": record["incarnation"],
                    }
                if entry["revision"] > floor:
                    facts.append(entry)
        erasures = [
            {k: v for k, v in t.items() if k != "revision"}
            for t in state["tombstones"] if int(t.get("revision", 0)) > floor
        ]
        chunks = [facts[i:i + BATCH_FACTS] for i in range(0, len(facts), BATCH_FACTS)] or [[]]
        for n, chunk in enumerate(chunks):
            last = n == len(chunks) - 1
            batch: Any = {
                "schema": "migration-facts/1", "batch_id": f"{self._scope}@{rev}#{n}",
                "facts": chunk, "erasures": erasures if last else [],
                "source_revision": rev, "is_final": last,
            }
            yield batch

    def migration_ingest(
        self, ctx: RequestContext, migration_id: str, envelope: MigrationEnvelope,
    ) -> MigrationResult:
        raise files_error(
            "capability_unsupported",
            "the files backend does not ingest migrations in wave A",
            remedy="declare facts individually",
        )

    # ------------------------------------------------------------ health

    def health(self) -> MemoryHealth:
        now = time.time()
        probe = self._tree
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        if not probe.is_dir() or not os.access(probe, os.W_OK | os.X_OK):
            return memory_health(
                "unreachable", f"{probe} cannot hold the memory tree", checked_at=now,
            )
        lock = self._state_dir / "locks" / "write.lock"
        try:
            if lock.exists():
                fd = os.open(lock, os.O_RDWR)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except BlockingIOError:
                    return memory_health("degraded", "the writer lock is held", checked_at=now)
                finally:
                    os.close(fd)
            jdir = self._journal_dir()
            if jdir.is_dir() and any(
                json.loads(p.read_bytes()).get("status") == "pending" for p in jdir.glob("*.json")
            ):
                return memory_health(
                    "degraded", "an interrupted write awaits recovery", checked_at=now,
                )
        except (OSError, ValueError) as exc:
            return memory_health("unreachable", f"{type(exc).__name__}: {exc}", checked_at=now)
        return memory_health("healthy", "tree reachable", checked_at=now)


def files_backend_factory(config: Mapping[str, Any]) -> FilesBackend:
    """Build a :class:`FilesBackend` from an attachment config. Does no I/O."""
    return FilesBackend(config)


__all__ = ["RECEIPT_TTL_SECONDS", "FilesBackend", "MemoryBackendError", "files_backend_factory"]
