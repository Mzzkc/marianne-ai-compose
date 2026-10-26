"""Submission exercises the HTTP boundary without any external network."""

import json
from pathlib import Path

import httpx
import pytest

from marianne.telemetry import ConsentRecord, TelemetryConfig
from marianne.telemetry.spool import TelemetrySpool
from marianne.telemetry.transmitter import MockTelemetryTransmitter, TelemetryTransmitter


@pytest.fixture
def staged(tmp_path):
    consent = ConsentRecord(tmp_path / "consent.json")
    consent.grant()
    config = TelemetryConfig(
        enabled=True,
        submit_mode="push",
        endpoint="https://telemetry.example/submit",
        spool_path=tmp_path / "spool.jsonl",
    )
    spool = TelemetrySpool(config.spool_path)
    envelope = {
        "schema_version": 1,
        "consent_id": consent.consent_id,
        "payload": {"job": {"status": "completed"}},
    }
    spool.append(envelope)
    return config, consent, spool, envelope


async def test_success_prunes_only_submitted_snapshot(staged):
    config, consent, spool, envelope = staged
    later = {**envelope, "payload": {"job": {"status": "failed"}}}
    seen = []

    def receive(request):
        seen.append(json.loads(request.content))
        spool.append(later)
        return httpx.Response(202)

    transmitter = TelemetryTransmitter(config, consent, transport=httpx.MockTransport(receive))
    assert await transmitter.flush() == 1
    assert seen == [{"envelopes": [envelope]}]
    assert spool.read() == [later]


@pytest.mark.parametrize("failure", ["timeout", "connection", "http", "unexpected"])
async def test_failure_retains_spool_and_warns(staged, failure, monkeypatch):
    from marianne.telemetry import transmitter as module

    config, consent, spool, envelope = staged
    warnings = []
    monkeypatch.setattr(module._logger, "warning", lambda *args, **kw: warnings.append(args))

    def receive(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("sensitive detail", request=request)
        if failure == "connection":
            raise httpx.ConnectError("sensitive detail", request=request)
        if failure == "unexpected":
            raise RuntimeError("sensitive detail")
        return httpx.Response(500)

    transmitter = TelemetryTransmitter(config, consent, transport=httpx.MockTransport(receive))
    assert await transmitter.flush() == -1
    assert spool.read() == [envelope]
    assert warnings
    assert "sensitive detail" not in str(warnings)


@pytest.mark.parametrize(
    "gate",
    [
        "disabled",
        "local_only",
        "unset",
        "revoked",
        "rotated",
        "missing_endpoint",
        "unsafe_endpoint",
    ],
)
async def test_no_network_without_current_consent_and_push(staged, gate):
    config, consent, spool, envelope = staged
    if gate == "disabled":
        config.enabled = False
    elif gate == "local_only":
        config.submit_mode = "local_only"
    elif gate == "unset":
        consent.path.unlink()
    elif gate in {"revoked", "rotated"}:
        consent.revoke()
        if gate == "rotated":
            consent.grant()
    else:
        config.endpoint = None if gate == "missing_endpoint" else "http://public.example/upload"
    transmitter = MockTelemetryTransmitter(config, consent)
    assert await transmitter.flush() <= 0
    assert transmitter.submissions == []
    assert spool.read() == [envelope]


async def test_dry_run_and_empty_flush_never_submit(staged):
    config, consent, spool, _ = staged
    transmitter = MockTelemetryTransmitter(config, consent)
    assert await transmitter.flush(dry_run=True) == 0
    assert transmitter.submissions == []
    spool.purge()
    assert await transmitter.flush() == 0
    assert transmitter.submissions == []


async def test_mock_double_exercises_prune_and_failure(staged):
    config, consent, spool, envelope = staged
    transmitter = MockTelemetryTransmitter(config, consent, succeed=False)
    assert await transmitter.flush() == -1
    assert spool.read() == [envelope]
    transmitter.succeed = True
    assert await transmitter.flush() == 1
    assert transmitter.submissions == [[envelope], [envelope]]
    assert spool.count() == 0


async def test_corrupt_spool_failure_is_isolated(staged):
    config, consent, spool, _ = staged
    spool.path.write_text("broken json\n")
    assert await MockTelemetryTransmitter(config, consent).flush() == -1
    assert spool.path.read_text() == "broken json\n"


def test_prune_does_not_erase_replaced_snapshot(tmp_path: Path):
    spool = TelemetrySpool(tmp_path / "spool.jsonl")
    spool.append({"first": 1})
    snapshot = spool.read()
    spool.purge()
    spool.append({"new": 2})
    assert not spool.acknowledge(snapshot)
    assert spool.read() == [{"new": 2}]


async def test_concurrent_flush_is_refused_without_duplicate_send(staged):
    import asyncio

    config, consent, spool, envelope = staged
    started = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def receive(request):
        calls.append(json.loads(request.content))
        started.set()
        await release.wait()
        return httpx.Response(200)

    transport = httpx.MockTransport(receive)
    first = asyncio.create_task(TelemetryTransmitter(config, consent, transport=transport).flush())
    await started.wait()
    second = await TelemetryTransmitter(config, consent, transport=transport).flush()
    release.set()
    assert await first == 1
    assert second == -1
    assert calls == [{"envelopes": [envelope]}]
    assert spool.count() == 0


async def test_revocation_after_snapshot_prevents_sending(staged, monkeypatch):
    config, consent, spool, envelope = staged
    original = TelemetrySpool.read

    def revoke_on_read(self):
        result = original(self)
        consent.revoke()
        return result

    monkeypatch.setattr(TelemetrySpool, "read", revoke_on_read)
    transmitter = MockTelemetryTransmitter(config, consent)
    assert await transmitter.flush() == -1
    assert transmitter.submissions == []
    assert original(spool) == [envelope]


async def test_snapshot_replaced_during_request_is_retained(staged):
    config, consent, spool, _ = staged

    def receive(request):
        spool.purge()
        spool.append({"new": 1})
        return httpx.Response(200)

    transmitter = TelemetryTransmitter(config, consent, transport=httpx.MockTransport(receive))
    assert await transmitter.flush() == -1
    assert spool.read() == [{"new": 1}]


async def test_empty_direct_submit_is_noop(staged):
    config, consent, _, _ = staged
    transmitter = MockTelemetryTransmitter(config, consent)
    assert not await transmitter.submit([])
    assert transmitter.submissions == []


async def test_revocation_between_consent_reads_refuses_missing_id_records(staged, monkeypatch):
    config, consent, _, _ = staged
    reads = iter([consent.consent_id, None])
    monkeypatch.setattr(ConsentRecord, "consent_id", property(lambda self: next(reads)))
    transmitter = MockTelemetryTransmitter(config, consent)
    assert not await transmitter.submit([{"payload": "missing ID"}])
    assert transmitter.submissions == []
