"""Private JSONL staging with a stable advisory lock and atomic replacement."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class TelemetrySpool:
    """Serialize writers across processes; a failed update preserves old bytes.

    Keep the sidecar lock across purge/replace: locking the replaced data inode
    would let a concurrent writer acquire a different inode and lose envelopes.
    No file or directory is created by construction or an empty read.
    """

    def __init__(self, path: Path | None = None, *, consent_path: Path | None = None) -> None:
        from marianne.telemetry.consent import ConsentRecord

        self.path = path if path is not None else Path.home() / ".marianne/telemetry.jsonl"
        consent_path = consent_path if consent_path is not None else ConsentRecord().path
        reserved = {consent_path.resolve(),
                    consent_path.with_name(consent_path.name + ".lock").resolve()}
        spool_paths = {self.path.resolve(),
                       self.path.with_name(self.path.name + ".lock").resolve(),
                       self.path.with_name(self.path.name + ".submit.lock").resolve()}
        if reserved & spool_paths:
            raise ValueError("Telemetry spool must not overlap consent state or locks")
        # resolve() catches a symlinked spool whose link name hides a .lock
        # target (or a symlinked parent directory renaming the final segment).
        if self.path.name.endswith(".lock") or self.path.resolve().name.endswith(".lock"):
            raise ValueError("Telemetry spool must not use a reserved .lock filename")

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock_path = self.path.with_name(self.path.name + ".lock")
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _read(self) -> list[dict[str, Any]]:
        if self.path.is_symlink():
            raise ValueError("Telemetry spool must not be a symbolic link")
        try:
            contents = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        records = [json.loads(line) for line in contents.splitlines() if line.strip()]
        if any(not isinstance(record, dict) for record in records):
            raise ValueError("Telemetry spool entries must be objects")
        return records

    def read(self) -> list[dict[str, Any]]:
        """Return staged records without changing or silently discarding them."""
        if not self.path.exists() and not self.path.is_symlink():
            return []
        with self._locked():
            return self._read()

    def count(self) -> int:
        """Number of complete staged envelopes."""
        return len(self.read())

    def append(
        self, envelope: dict[str, Any], *, authorize: Callable[[], bool] | None = None,
    ) -> bool:
        """Durably append, rechecking permission after waiting for the lock."""
        with self._locked():
            if authorize is not None and not authorize():
                return False
            records = self._read()
            records.append(envelope)
            self._write(records)
        return True

    def _write(self, records: list[dict[str, Any]]) -> None:
        fd, name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                for record in records:
                    stream.write(json.dumps(record, ensure_ascii=True, allow_nan=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def acknowledge(self, snapshot: list[dict[str, Any]]) -> bool:
        """Prune only an unchanged submitted prefix, retaining concurrent appends."""
        with self._locked():
            records = self._read()
            if records[:len(snapshot)] != snapshot:
                return False
            self._write(records[len(snapshot):])
        return True

    @contextmanager
    def submission(self) -> Iterator[bool]:
        """Refuse overlapping flushes without holding the writer lock over HTTP."""
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = self.path.with_name(self.path.name + ".submit.lock")
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
            else:
                yield True
        finally:
            os.close(fd)

    def purge(self) -> None:
        """Remove staged data, retaining the stable writer lock."""
        # A first append can hold the writer lock before the data file exists.
        # Revocation must wait for that writer too, then remove its staged data.
        lock_path = self.path.with_name(self.path.name + ".lock")
        if not self.path.exists() and not self.path.is_symlink() and not lock_path.exists():
            return
        with self._locked():
            self.path.unlink(missing_ok=True)
