"""``CamBackend``: ``memory-backend/1.1`` served by CAM over its HTTP API.

Licence boundary. CAM is a separate program under its own licence. Marianne
reaches it only across a process boundary, by HTTP on the wire format CAM
documents, using the standard library alone. Nothing of CAM is imported,
copied, vendored or declared as a dependency, and a test keeps it that way.

Identity. The gateway has already authorised every call before it gets here.
The CAM key is the conductor's own, so until CAM ships stamped principals
(delegated principals are not built) every Marianne principal appears to CAM
as the one key's principal. This client does not invent principal stamping.
The scope is passed through as CAM's scope.

What CAM offers today: declare, recall, and a readiness probe. ``verify``,
``bind``, ``forget``, ``fetch``, ``stream_facts`` and ``migration_ingest`` have
no CAM operation yet, and each raises E7 ``capability_unsupported`` naming the
operation. Recall has no cursor in CAM, so a response is trimmed to the
per-call byte cap at a fact boundary on this side: omitted facts are reported
by a ``page`` cursor with an empty token (nothing to resume) and an ``omitted``
count, and a single fact larger than the cap is listed as ``oversized_unit``.

The API key is named by an environment variable or a file, read for each
request, and never appears in a log, an exception message or a ``repr``.
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import ssl
import time
import urllib.parse
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

from marianne.memory.backend_protocol import RequestContext
from marianne.memory.errors import (
    ERROR_CLASSES,
    BackendUnreachable,
    ErrorCode,
    MemoryBackendError,
    SchemaViolation,
    Unauthenticated,
)
from marianne.memory.model import (
    BindResult,
    DeclareResult,
    FetchResult,
    ForgetResult,
    MemoryHealth,
    MigrationBatch,
    MigrationEnvelope,
    MigrationResult,
    OversizedUnit,
    Permission,
    Provenance,
    RecallItem,
    RecallResult,
    VerifyResult,
    content_hash,
    memory_health,
    oversized_unit_cursor,
    page_cursor,
    parse_scope,
)

_LOG = logging.getLogger(__name__)

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 10.0
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost"})

#: CAM error kind -> (errors/1 code, Marianne subtype). Anything not listed is
#: E5 with the CAM kind as the subtype.
CAM_SUBTYPES: Mapping[str, ErrorCode] = {
    "auth_required": ErrorCode.E1,
    "auth_revoked": ErrorCode.E8,
    "permission_denied": ErrorCode.E2,
    "scope_unknown": ErrorCode.E3,
    "scope_ambiguous": ErrorCode.E3,
    "evidence_missing": ErrorCode.E7,
    "evidence_unavailable": ErrorCode.E7,
    "edge_invalid": ErrorCode.E7,
    "config_invalid": ErrorCode.E7,
    "visibility_choice_required": ErrorCode.E7,
    "fact_not_found": ErrorCode.E11,
}


def _config_error(detail: str, remedy: str = "") -> SchemaViolation:
    return SchemaViolation(detail, subtype="config_invalid", remedy=remedy)


@dataclass(frozen=True)
class CamBackendConfig:
    """One CAM attachment. Holds the NAME of the key source, never the key."""

    base_url: str
    api_key_env: str = ""
    api_key_file: str = ""
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    allow_remote: bool = False

    def __post_init__(self) -> None:
        if bool(self.api_key_env) == bool(self.api_key_file):
            raise _config_error(
                "name exactly one of api_key_env and api_key_file",
                "set api_key_env to an environment variable name, or api_key_file to a path",
            )
        if not isinstance(self.timeout_seconds, (int, float)) or isinstance(
            self.timeout_seconds, bool
        ) or not 0 < self.timeout_seconds <= 3600:
            raise _config_error("timeout_seconds must be a number in (0, 3600]")
        try:
            parts = urllib.parse.urlsplit(self.base_url)
            host = parts.hostname
            parts.port  # noqa: B018 - validates the port
        except ValueError:
            raise _config_error("base_url is not a valid URL") from None
        if parts.scheme not in ("http", "https") or not host:
            raise _config_error("base_url must be an http:// or https:// URL with a host")
        if parts.username is not None or parts.password is not None:
            raise _config_error("base_url must not carry credentials")
        if parts.query or parts.fragment:
            raise _config_error("base_url must not carry a query or fragment")
        if host not in _LOOPBACK_HOSTS:
            if not self.allow_remote:
                raise _config_error(
                    "base_url is not loopback (127.0.0.1 or localhost)",
                    "set allow_remote: true and use https:// to reach a remote CAM",
                )
            if parts.scheme != "https":
                raise _config_error(
                    "a non-loopback CAM must be reached over https://",
                    "use an https:// base_url",
                )


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "[redacted]") if secret else text


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat()


def _unsupported(operation: str) -> NoReturn:
    raise SchemaViolation(
        f"CAM does not offer `{operation}` yet; this backend cannot perform it",
        subtype="capability_unsupported",
        remedy="use a backend that supports it, or wait for CAM to ship that operation",
    )


class CamBackend:
    """``memory-backend/1.1`` over CAM's HTTP API (declare, recall, health)."""

    def __init__(self, config: CamBackendConfig) -> None:
        self._cfg = config
        parts = urllib.parse.urlsplit(config.base_url)
        self._https = parts.scheme == "https"
        self._host = parts.hostname or ""
        self._port = parts.port
        self._prefix = parts.path.rstrip("/")

    def __repr__(self) -> str:
        return f"CamBackend(base_url={self._cfg.base_url!r})"

    # -------------------------------------------------------------- transport

    def _key(self) -> str:
        cfg = self._cfg
        if cfg.api_key_env:
            key = os.environ.get(cfg.api_key_env, "").strip()
            if not key:
                raise Unauthenticated(
                    f"environment variable {cfg.api_key_env} is unset or empty",
                    subtype="key_unavailable",
                    remedy=f"export {cfg.api_key_env} in the conductor's environment",
                )
            return key
        try:
            key = Path(cfg.api_key_file).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as exc:
            raise Unauthenticated(
                f"key file {cfg.api_key_file} cannot be read ({type(exc).__name__})",
                subtype="key_unavailable",
                remedy="create the key file readable by the conductor",
            ) from None
        if not key:
            raise Unauthenticated(
                f"key file {cfg.api_key_file} is empty", subtype="key_unavailable"
            )
        return key

    def _timeout(self, ctx: RequestContext | None) -> float:
        if ctx is None:
            return float(self._cfg.timeout_seconds)
        remaining = ctx.remaining_seconds
        if remaining <= 0:
            raise ERROR_CLASSES[ErrorCode.E10](
                "the call deadline has passed; no request was sent",
                subtype="deadline_exceeded",
                remedy="retry with a later deadline",
            )
        return min(float(self._cfg.timeout_seconds), remaining)

    def _request(
        self, method: str, path: str, ctx: RequestContext | None, body: Any = None,
    ) -> dict[str, Any]:
        timeout = self._timeout(ctx)
        key = self._key()
        headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
        payload: bytes | None = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(payload))
        conn: http.client.HTTPConnection
        if self._https:
            conn = http.client.HTTPSConnection(
                self._host, self._port, timeout=timeout, context=ssl.create_default_context(),
            )
        else:
            conn = http.client.HTTPConnection(self._host, self._port, timeout=timeout)
        try:
            try:
                conn.request(method, self._prefix + path, body=payload, headers=headers)
                resp = conn.getresponse()
                raw = resp.read(MAX_RESPONSE_BYTES + 1)
                status = resp.status
            except TimeoutError:
                raise BackendUnreachable(
                    f"CAM did not answer within {timeout:.1f}s",
                    subtype="timeout",
                    remedy="check that cam-server is running and responsive",
                ) from None
            except (OSError, http.client.HTTPException) as exc:
                raise BackendUnreachable(
                    f"CAM at {self._cfg.base_url} is unreachable ({type(exc).__name__})",
                    subtype="connection_failed",
                    remedy="start cam-server and check base_url",
                ) from None
        finally:
            conn.close()
        return self._decode(status, raw, key)

    def _decode(self, status: int, raw: bytes, key: str) -> dict[str, Any]:
        data: Any = None
        malformed = len(raw) > MAX_RESPONSE_BYTES
        if not malformed:
            try:
                data = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                malformed = True
        if malformed or not isinstance(data, dict):
            raise BackendUnreachable(
                f"CAM answered HTTP {status} with a body that is not the expected JSON object",
                subtype="malformed_response",
            )
        if status >= 400 or "error" in data:
            self._raise_cam_error(status, data, key)
        return data

    def _raise_cam_error(self, status: int, data: dict[str, Any], key: str) -> NoReturn:
        err = data.get("error")
        if not isinstance(err, dict) or not isinstance(err.get("kind"), str):
            raise BackendUnreachable(
                f"CAM answered HTTP {status} without a typed error", subtype="malformed_response",
            )
        kind = str(err["kind"])
        message = _redact(str(err.get("message", "")), key)
        remedy = _redact(str(err.get("remedy", "") or ""), key)
        if kind == "not_implemented_yet":
            raise SchemaViolation(
                f"CAM does not offer this operation yet: {message}",
                subtype="capability_unsupported",
                remedy=remedy,
            )
        code = CAM_SUBTYPES.get(kind)
        if code is None:
            raise BackendUnreachable(message or f"CAM error {kind}", subtype=kind, remedy=remedy)
        raise ERROR_CLASSES[code](message or kind, subtype=kind, remedy=remedy)

    # -------------------------------------------------------------- operations

    def declare(
        self,
        ctx: RequestContext,
        fact: str,
        scope: str,
        provenance: Provenance | None = None,
        confidence: float = 1.0,
        tags: list[str] | None = None,
    ) -> DeclareResult:
        ctx.require(Permission.WRITE)
        canonical = parse_scope(scope).canonical
        if not isinstance(fact, str) or not fact.strip():
            raise SchemaViolation("fact text is empty", subtype="fact_invalid")
        prov: Mapping[str, Any] = provenance or {}
        locator = str(prov.get("evidence_locator") or "")
        claim = str(prov.get("claim_digest") or content_hash(fact))
        observed = prov.get("observed_at")
        evidence: dict[str, str] = {}
        if locator:
            evidence["locator"] = locator
            evidence["content_hash"] = claim if claim.startswith("sha256:") else f"sha256:{claim}"
            evidence["observed_at"] = _iso(
                float(observed) if isinstance(observed, (int, float)) else time.time()
            )
        out = self._request(
            "POST", "/v1/insights", ctx,
            {"content": fact, "evidence": evidence, "scope": canonical},
        )
        fact_id = out.get("fact_id")
        if not isinstance(fact_id, str) or not fact_id:
            raise BackendUnreachable(
                "CAM's declare answer carries no fact_id", subtype="malformed_response"
            )
        now = time.time()
        author = str(out.get("contributor") or "")
        return {
            "fact_id": fact_id,
            "hash": content_hash(fact),
            "written_at": now,
            "tier": "cam",
            "status": "acknowledged",
            "provenance_receipt": {
                "fact_id": fact_id,
                "claim_digest": claim,
                "evidence_locator": locator,
                "author": author,
                "written_at": now,
            },
        }

    def recall(
        self,
        ctx: RequestContext,
        query: str,
        scope: str,
        limit: int = 100,
        cursor: str | None = None,
        cap_bytes: int | None = None,
    ) -> RecallResult:
        ctx.require(Permission.READ)
        canonical = parse_scope(scope).canonical
        if cursor is not None:
            raise SchemaViolation(
                "CAM recall has no cursor; this backend never issues one",
                subtype="cursor_invalid",
                remedy="recall again without a cursor",
            )
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise SchemaViolation("limit must be a positive integer", subtype="argument_invalid")
        if cap_bytes is not None and (
            not isinstance(cap_bytes, int) or isinstance(cap_bytes, bool) or cap_bytes < 0
        ):
            raise SchemaViolation(
                "cap_bytes must be a non-negative integer", subtype="argument_invalid"
            )
        out = self._request(
            "POST", "/v1/recall", ctx,
            {"kind": "semantic", "query": query, "scope": canonical,
             "max_results": limit, "include_paths": True},
        )
        raw = out.get("results")
        if not isinstance(raw, list):
            raise BackendUnreachable(
                "CAM's recall answer carries no results list", subtype="malformed_response"
            )
        items: list[RecallItem] = []
        oversized: list[OversizedUnit] = []
        used = 0
        omitted = 0
        stopped = False
        for entry in raw:
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("fact_id"), str)
                or not isinstance(entry.get("content"), str)
            ):
                raise BackendUnreachable(
                    "CAM's recall answer has a malformed result", subtype="malformed_response"
                )
            size = len(entry["content"].encode("utf-8"))
            if stopped:
                omitted += 1
                continue
            if cap_bytes is not None and used + size > cap_bytes:
                if size > cap_bytes:
                    oversized.append(oversized_unit_cursor(entry["fact_id"], size, cap_bytes))
                    continue
                stopped = True
                omitted += 1
                continue
            used += size
            score = entry.get("score")
            item: RecallItem = {
                "fact_id": entry["fact_id"],
                "content": entry["content"],
                "score": float(score) if isinstance(score, (int, float)) else 0.0,
                "tier": "cam",
                "asserted_at": 0.0,
                "evidence_locator": "",
                "status": "live",
            }
            path = entry.get("explaining_path")
            if isinstance(path, dict):
                item["explaining_path"] = path
            items.append(item)
        page = page_cursor("", "cam:recall", omitted) if omitted else None
        return {
            "results": items,
            "cursor": page,
            "oversized": oversized,
            "explaining_path": None,
        }

    def verify(
        self, ctx: RequestContext, fact_id_or_hash: str, expected_evidence: str = "",
    ) -> VerifyResult:
        _unsupported("verify")

    def bind(
        self,
        ctx: RequestContext,
        fact_id: str,
        target_symbol: str,
        evidence_receipt: str | None = None,
    ) -> BindResult:
        _unsupported("bind")

    def forget(self, ctx: RequestContext, fact_id: str, reason: str = "") -> ForgetResult:
        _unsupported("forget")

    def fetch(self, ctx: RequestContext, unit_id: str, offset: int, length: int) -> FetchResult:
        _unsupported("fetch")

    def stream_facts(
        self, ctx: RequestContext, scope: str, since_revision: int | None = None,
    ) -> Iterator[MigrationBatch]:
        _unsupported("stream_facts")

    def migration_ingest(
        self, ctx: RequestContext, migration_id: str, envelope: MigrationEnvelope,
    ) -> MigrationResult:
        _unsupported("migration_ingest")

    def health(self) -> MemoryHealth:
        now = time.time()
        try:
            out = self._request("GET", "/v1/health/ready", None)
        except Unauthenticated as exc:
            return memory_health("unauthenticated", exc.detail, checked_at=now)
        except MemoryBackendError as exc:
            return memory_health("unreachable", exc.detail, checked_at=now)
        if out.get("embedding_provider") == "absent":
            return memory_health(
                "degraded", "CAM recall is lexical: no embedding provider", checked_at=now,
            )
        if out.get("storage") not in (None, "ok"):
            return memory_health("degraded", f"CAM storage is {out.get('storage')}", checked_at=now)
        return memory_health("healthy", "CAM ready", checked_at=now)


def cam_backend_factory(settings: Mapping[str, Any]) -> CamBackend:
    """Build a :class:`CamBackend` from an attachment's ``settings:``. Does no I/O.

    Keys: ``base_url`` (required), ``api_key_env`` | ``api_key_file`` (exactly
    one; the NAME of a variable or a path, never the key), ``timeout_seconds``,
    ``allow_remote``.
    """
    allowed = {"base_url", "api_key_env", "api_key_file", "timeout_seconds", "allow_remote"}
    unknown = sorted(set(settings) - allowed)
    if unknown:
        raise _config_error(f"unknown cam setting(s) {unknown}; legal: {sorted(allowed)}")
    if not isinstance(settings.get("base_url"), str):
        raise _config_error("cam settings need base_url")
    for name in ("api_key_env", "api_key_file"):
        if name in settings and not isinstance(settings[name], str):
            raise _config_error(f"{name} must be a string")
    if "allow_remote" in settings and not isinstance(settings["allow_remote"], bool):
        raise _config_error("allow_remote must be true or false")
    return CamBackend(CamBackendConfig(
        base_url=settings["base_url"],
        api_key_env=settings.get("api_key_env", ""),
        api_key_file=settings.get("api_key_file", ""),
        timeout_seconds=settings.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
        allow_remote=settings.get("allow_remote", False),
    ))
