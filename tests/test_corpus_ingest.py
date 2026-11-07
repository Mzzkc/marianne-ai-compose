"""Telemetry ingestion reuses sanitization and keeps consent lineage opaque."""

import hashlib
import json

import pytest
import yaml

from marianne.corpus.ingest import dedupe, ingest_spool
from marianne.corpus.schema import TrainingExample


def envelope(score=None):
    return {
        "schema_version": 1,
        "collected_at": "2026-11-05T00:00:00+00:00",
        "consent_id": "private-grant-id",
        "payload": {"score": score or {"name": "example"}, "job": {}, "sheets": []},
    }


def test_ingestion_sanitizes_prose_keys_and_transport_settings(tmp_path):
    secret = "aB3d" * 10
    raw = envelope(
        {
            "name": "example",
            "description": f"hello {secret} alice@example.test",
            "access_key": "short-secret",
            "telemetry": {"endpoint": "private receiver"},
        }
    )
    spool = tmp_path / "telemetry.jsonl"
    spool.write_text(json.dumps(raw) + "\n")
    (record,) = ingest_spool(spool)
    score = yaml.safe_load(record.output)
    assert score["access_key"] == "[SECRET]"
    assert "telemetry" not in score
    blob = record.model_dump_json()
    assert all(value not in blob for value in (secret, "alice@example.test", "private-grant-id"))
    assert "[SECRET]" in blob
    assert record.provenance.source_kind == "telemetry"
    assert record.provenance.source_schema_version == 1
    assert record.provenance.consent_hash == hashlib.sha256(b"private-grant-id").hexdigest()
    assert record.provenance.content_hash == hashlib.sha256(record.output.encode()).hexdigest()
    assert raw["payload"]["score"]["access_key"] == "short-secret"


@pytest.mark.parametrize(
    "bad",
    [
        "broken-json",
        "[]",
        "null",
        "{}",
        json.dumps({**envelope(), "schema_version": 2}),
        json.dumps({**envelope(), "consent_id": ""}),
        json.dumps({**envelope(), "consent_id": None}),
        json.dumps({**envelope(), "payload": {"score": []}}),
        json.dumps({**envelope(), "payload": {"score": {}}}),
    ],
)
def test_invalid_lines_are_counted_and_skipped_without_logging_contents(tmp_path, caplog, bad):
    spool = tmp_path / "telemetry.jsonl"
    spool.write_text(bad + "\n\n" + json.dumps(envelope()) + "\n")
    records = ingest_spool(spool)
    assert len(records) == 1
    assert "Skipped 1 invalid telemetry record" in caplog.text
    assert "private-grant-id" not in caplog.text


def test_dedupe_prefers_repository_even_if_telemetry_is_first(tmp_path):
    spool = tmp_path / "telemetry.jsonl"
    spool.write_text(json.dumps(envelope()) + "\n")
    (telemetry,) = ingest_spool(spool)
    local = TrainingExample(
        instruction="Compose",
        output=telemetry.output,
        rationale="Human WHY",
        provenance={"source_path": "examples/test.yaml", "source_kind": "in_repo"},
    )
    assert dedupe([telemetry, local, telemetry]) == [local]
    assert dedupe([local, telemetry]) == [local]
    different = local.model_copy(update={"output": "name: different\n"})
    assert dedupe([local, different, local]) == [local, different]


def test_invalid_utf8_line_does_not_discard_later_valid_envelopes(tmp_path, caplog):
    spool = tmp_path / "telemetry.jsonl"
    spool.write_bytes(b"\xff\n" + (json.dumps(envelope()) + "\n").encode())
    assert len(ingest_spool(spool)) == 1
    assert "Skipped 1 invalid telemetry record" in caplog.text
