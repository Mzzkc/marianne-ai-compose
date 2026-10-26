"""Explicit user consent, read fresh at each collection/submission boundary."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from uuid import UUID, uuid4


class ConsentState(str, Enum):
    """Missing or unreadable records never imply permission."""

    UNSET = "unset"
    GRANTED = "granted"
    REVOKED = "revoked"


class ConsentRecord:
    """Atomic, private consent record independent of any score's contents.

    Do not cache this object’s state: long-running daemons must observe revocation.
    A caller may override the path for isolated tests, never via score YAML.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path if path is not None else Path.home() / ".marianne/telemetry-consent.json"

    def _read(self) -> tuple[ConsentState, str | None]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return ConsentState.UNSET, None
            state = ConsentState(raw.get("state"))
            if state is ConsentState.GRANTED:
                identity = str(UUID(raw["consent_id"]))
                return state, identity
            return state, None
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            return ConsentState.UNSET, None

    @property
    def state(self) -> ConsentState:
        """Return current on-disk consent, failing closed on malformed data."""
        return self._read()[0]

    @property
    def consent_id(self) -> str | None:
        """Opaque identifier for this grant; absent after revocation."""
        return self._read()[1]

    def _write(self, state: ConsentState, identity: str | None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({
                    "state": state.value,
                    "consent_id": identity,
                    "updated_at": datetime.now(UTC).isoformat(),
                }, stream)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def grant(self) -> None:
        """Persist explicit permission, retaining the ID of an existing grant."""
        with self.collection_transaction():
            self._write(ConsentState.GRANTED, self.consent_id or str(uuid4()))

    @contextmanager
    def collection_transaction(self) -> Iterator[None]:
        """Serialize grant/revoke with authorized appends across every spool.

        Always acquire this before a spool lock. Revocation then waits for any
        already-authorized append and no append can finish after revoke returns.
        """
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock_path = self.path.with_name(self.path.name + ".lock")
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def revoke(self, *, spool_path: Path | None = None, purge: bool = False) -> None:
        """Stop collection first, then optionally remove the supplied local spool."""
        with self.collection_transaction():
            self._write(ConsentState.REVOKED, None)
            if purge and spool_path is not None:
                self.purge_spool(spool_path)

    @staticmethod
    def purge_spool(path: Path) -> None:
        """Remove staged envelopes; a missing spool is already purged."""
        from marianne.telemetry.spool import TelemetrySpool

        TelemetrySpool(path).purge()
