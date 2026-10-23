"""Functional review: exercise collection through the actual HTTP serialization seam."""

import json
from pathlib import Path

import httpx
import pytest

from marianne.core.config import JobConfig
from marianne.telemetry import ConsentRecord, TelemetryCollector
from marianne.telemetry.spool import TelemetrySpool
from marianne.telemetry.transmitter import TelemetryTransmitter


@pytest.fixture
def pipeline(tmp_path):
    config = JobConfig.model_validate({
        "name": "review", "instrument": "ollama", "workspace": str(tmp_path),
        "sheet": {"size": 1, "total_items": 1},
        "prompt": {"template": "Read /home/reviewer/private.txt TOKEN=review-secret"},
        "telemetry": {"enabled": True, "spool_path": str(tmp_path / "spool.jsonl"),
                      "endpoint": "https://receiver.example/submit"},
    })
    consent = ConsentRecord(tmp_path / "consent.json")
    consent.grant()
    return config, consent, TelemetrySpool(config.telemetry.spool_path)


@pytest.mark.parametrize("status", ["completed", "failed"])
async def test_collected_envelope_reaches_http_with_metrics_and_redaction(pipeline, status):
    config, consent, spool = pipeline
    sheets = [{"status": status, "attempt_count": 2, "validation_passed": status == "completed",
               "error_message": "Inspect /home/reviewer/output.txt PASSWORD=review-password"}]
    before = config.model_dump(mode="json")
    assert TelemetryCollector(consent).collect(config, status, sheets)
    staged = spool.read()
    received = []

    def receive(request):
        received.append(json.loads(request.content))
        assert request.method == "POST"
        return httpx.Response(202)

    transmitter = TelemetryTransmitter(
        config.telemetry, consent, transport=httpx.MockTransport(receive),
    )
    assert await transmitter.flush() == 0
    assert received == []
    assert spool.read() == staged
    config.telemetry.submit_mode = "push"
    assert await transmitter.flush() == 1
    assert received == [{"envelopes": staged}]
    sent = received[0]["envelopes"][0]
    assert sent["schema_version"] == 1
    assert sent["payload"]["job"] == {"status": status, "sheet_count": 1}
    assert sent["payload"]["sheets"][0]["attempt_count"] == 2
    assert sent["payload"]["sheets"][0]["validation_passed"] is (status == "completed")
    for private in ("/home/reviewer", "review-secret", "review-password", str(config.workspace)):
        assert private not in json.dumps(sent)
    assert config.prompt.model_dump(mode="json") == before["prompt"]
    assert sheets[0]["error_message"].endswith("PASSWORD=review-password")
    assert spool.count() == 0


async def test_regrant_requires_purge_then_new_collection_recovers(pipeline):
    config, consent, spool = pipeline
    config.telemetry.submit_mode = "push"
    collector = TelemetryCollector(consent)
    assert collector.collect(config, "completed", [])
    old = spool.read()
    consent.revoke()
    assert not collector.collect(config, "failed", [])
    consent.grant()
    calls = []

    def receive(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200)

    transmitter = TelemetryTransmitter(
        config.telemetry, consent, transport=httpx.MockTransport(receive),
    )
    assert await transmitter.flush() == -1
    assert calls == []
    assert spool.read() == old
    spool.purge()
    assert collector.collect(config, "failed", [])
    assert await transmitter.flush() == 1
    assert calls[0]["envelopes"][0]["consent_id"] != old[0]["consent_id"]
    assert spool.count() == 0


async def test_acknowledgment_disk_failure_preserves_batch_for_retry(pipeline, monkeypatch):
    config, consent, spool = pipeline
    config.telemetry.submit_mode = "push"
    assert TelemetryCollector(consent).collect(config, "completed", [])
    original_bytes = spool.path.read_bytes()
    requests = []

    def receive(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200)

    transmitter = TelemetryTransmitter(
        config.telemetry, consent, transport=httpx.MockTransport(receive),
    )
    original_replace = Path.replace

    def unavailable(self, target):
        if target == spool.path:
            raise OSError("simulated acknowledgment disk failure")
        return original_replace(self, target)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", unavailable)
        assert await transmitter.flush() == -1
    assert spool.path.read_bytes() == original_bytes
    assert not list(spool.path.parent.glob(".spool.jsonl.*"))
    assert await transmitter.flush() == 1
    assert requests[0] == requests[1]
    assert spool.count() == 0


@pytest.mark.parametrize("contents", ["[]", '{"state":"granted","consent_id":"bad"}', "null"])
async def test_corrupted_consent_stops_existing_collector_and_transmitter(pipeline, contents):
    config, consent, spool = pipeline
    config.telemetry.submit_mode = "push"
    collector = TelemetryCollector(consent)
    assert collector.collect(config, "completed", [])
    before = spool.path.read_bytes()
    consent.path.write_text(contents)

    def forbidden(request):
        pytest.fail("malformed consent must never reach HTTP")

    assert not collector.collect(config, "failed", [])
    transmitter = TelemetryTransmitter(
        config.telemetry, consent, transport=httpx.MockTransport(forbidden),
    )
    assert await transmitter.flush() == 0
    assert spool.path.read_bytes() == before
