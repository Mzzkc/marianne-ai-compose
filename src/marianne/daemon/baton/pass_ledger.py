"""Pass ledger for conditional sheet execution (GH #570).

Maintains a durable, workspace-scoped record of each sheet's last successful pass
under `<workspace>/.marianne/pass-ledger/<scope>/m<movement>[-v<voice>].yaml`.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

import structlog
import yaml
from pydantic import BaseModel, ConfigDict

SCHEMA_VERSION = 1
KIND = "marianne-pass-ledger-record"

_logger = structlog.get_logger(__name__)


def scope_for(config_path: Path | None, job_id: str) -> str:
    """Derive the ledger scope name for a job.

    Scope names the score file so chain children and schedule ticks share
    the ledger. Without a config path it falls back to the job id.
    """
    if config_path is not None:
        stem = re.sub(r"[^a-zA-Z0-9_.-]", "-", config_path.stem)
        path_hash = hashlib.sha256(str(config_path.resolve()).encode("utf-8")).hexdigest()[:10]
        return f"{stem}-{path_hash}"
    return re.sub(r"[^a-zA-Z0-9_.-]", "-", job_id)


class PassRecord(BaseModel):
    """Record of a sheet's successful pass on a first attempt."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    kind: Literal["marianne-pass-ledger-record"] = "marianne-pass-ledger-record"
    job_id: str
    ledger_scope: str
    sheet_num: int
    movement: int
    voice: int | None
    workspace: str
    marianne_version: str
    passed_at: str
    attempt: int
    inputs: dict[str, str]
    sources: list[dict[str, str]] = []
    products: dict[str, str] = {}
    stdout_tail: str = ""


def _atomic_write_yaml(path: Path, document: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(raw_temp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            yaml.safe_dump(document, handle, sort_keys=False, allow_unicode=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


class PassLedger:
    """Workspace pass ledger under `<workspace>/.marianne/pass-ledger/<scope>`."""

    def __init__(self, root: Path) -> None:
        self.root = root

    @staticmethod
    def job_root(workspace: Path, scope: str) -> Path:
        """Derive the ledger root directory for a given workspace and scope."""
        return workspace / ".marianne" / "pass-ledger" / scope

    def path(self, movement: int, voice: int | None) -> Path:
        """Derive the record path for a `(movement, voice)` key."""
        filename = f"m{movement}.yaml" if voice is None else f"m{movement}-v{voice}.yaml"
        return self.root / filename

    async def lookup(self, movement: int, voice: int | None) -> PassRecord | None:
        """Look up a pass record. Returns None on missing, unreadable or version mismatch."""
        record_path = self.path(movement, voice)
        return await asyncio.to_thread(self._sync_lookup, record_path)

    def _sync_lookup(self, path: Path) -> PassRecord | None:
        if not path.is_file():
            return None
        try:
            raw = path.read_text(encoding="utf-8")
            data = yaml.safe_load(raw)
            if not isinstance(data, dict):
                _logger.warning(
                    "pass_ledger.record_unreadable", path=str(path), reason="not a dict"
                )
                return None
            if data.get("schema_version") != SCHEMA_VERSION or data.get("kind") != KIND:
                _logger.warning(
                    "pass_ledger.record_unreadable", path=str(path), reason="schema mismatch"
                )
                return None
            return PassRecord.model_validate(data)
        except Exception as exc:  # noqa: BLE001 -- fallback to None on corrupt or unreadable record
            _logger.warning("pass_ledger.record_unreadable", path=str(path), error=str(exc))
            return None

    async def write(self, record: PassRecord) -> Path:
        """Atomically write a pass record to disk."""
        record_path = self.path(record.movement, record.voice)
        doc = record.model_dump(mode="json")
        await asyncio.to_thread(_atomic_write_yaml, record_path, doc)
        return record_path

    async def invalidate(self, keys: Iterable[tuple[int, int | None]]) -> int:
        """Delete records for the given `(movement, voice)` keys. Returns count removed."""
        return await asyncio.to_thread(self._sync_invalidate, keys)

    def _sync_invalidate(self, keys: Iterable[tuple[int, int | None]]) -> int:
        removed = 0
        for movement, voice in keys:
            p = self.path(movement, voice)
            if p.is_file():
                try:
                    p.unlink()
                    removed += 1
                except OSError as exc:
                    _logger.error("pass_ledger.invalidate_failed", path=str(p), error=str(exc))
                    raise
        return removed

    async def clear(self) -> None:
        """Remove the scope ledger directory completely."""
        await asyncio.to_thread(self._sync_clear)

    def _sync_clear(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)
