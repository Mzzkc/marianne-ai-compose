"""Re-sanitize consent-bearing telemetry envelopes before corpus assembly."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

from marianne.corpus.extract import COMPOSITION_INSTRUCTION
from marianne.corpus.schema import CorpusProvenance, TrainingExample
from marianne.telemetry.sanitizer import sanitize_score

_logger = logging.getLogger(__name__)


class _Payload(BaseModel):
    score: dict[str, Any] = Field(min_length=1)


class _Envelope(BaseModel):
    schema_version: Literal[1]
    consent_id: str = Field(min_length=1)
    payload: _Payload


def ingest_spool(path: Path) -> list[TrainingExample]:
    """Read explicitly selected local JSONL; skip and count invalid envelopes.

    A collection identifier is lineage, not permission for training or sharing.
    This operation sends nothing and does not consult or change current consent.
    It never copies envelope prose or transport settings into the corpus.
    """
    records = []
    skipped = 0
    with path.open("rb") as stream:
        for line in stream:
            if not line.strip():
                continue
            try:
                envelope = _Envelope.model_validate_json(line)
            except ValidationError:
                skipped += 1
                continue
            output = yaml.safe_dump(
                sanitize_score(envelope.payload.score),
                sort_keys=True,
                allow_unicode=True,
            )
            records.append(
                TrainingExample(
                    instruction=COMPOSITION_INSTRUCTION,
                    output=output,
                    provenance=CorpusProvenance(
                        source_path=str(path),
                        source_kind="telemetry",
                        source_schema_version=envelope.schema_version,
                        content_hash=hashlib.sha256(output.encode("utf-8")).hexdigest(),
                        consent_hash=hashlib.sha256(
                            envelope.consent_id.encode("utf-8")
                        ).hexdigest(),
                    ),
                )
            )
    if skipped:
        _logger.warning("Skipped %d invalid telemetry record(s).", skipped)
    return records


def dedupe(examples: Iterable[TrainingExample]) -> list[TrainingExample]:
    """Deduplicate exact output text; a repository example wins across sources."""
    unique: dict[str, TrainingExample] = {}
    for example in examples:
        digest = hashlib.sha256(example.output.encode("utf-8")).hexdigest()
        previous = unique.get(digest)
        if previous is None or (
            previous.provenance.source_kind == "telemetry"
            and example.provenance.source_kind == "in_repo"
        ):
            unique[digest] = example
    return list(unique.values())
