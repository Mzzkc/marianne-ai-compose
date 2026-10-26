"""Consent is persisted independently of score configuration and fails closed."""

import json
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


def test_consent_lifecycle_and_purge(tmp_path: Path) -> None:
    from marianne.telemetry import ConsentRecord, ConsentState

    path = tmp_path / "settings" / "consent.json"
    record = ConsentRecord(path)
    assert record.state is ConsentState.UNSET
    assert record.consent_id is None
    assert not path.exists()
    record.grant()
    identity = record.consent_id
    assert identity
    assert ConsentRecord(path).state is ConsentState.GRANTED
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    record.grant()
    assert record.consent_id == identity
    spool = tmp_path / "spool.jsonl"
    spool.write_text('{"payload": "example"}\n')
    record.revoke(spool_path=spool, purge=True)
    assert record.state is ConsentState.REVOKED
    assert record.consent_id is None
    assert not spool.exists()
    record.grant()
    assert record.consent_id != identity


def test_revocation_visible_to_existing_reader(tmp_path: Path) -> None:
    from marianne.telemetry import ConsentRecord, ConsentState

    reader = ConsentRecord(tmp_path / "consent.json")
    writer = ConsentRecord(reader.path)
    writer.grant()
    assert reader.state is ConsentState.GRANTED
    writer.revoke()
    assert reader.state is ConsentState.REVOKED


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        "null",
        "{}",
        '{"state":"granted"}',
        '{"state":"unknown","consent_id":"abc"}',
    ],
)
def test_invalid_consent_fails_closed(tmp_path: Path, content: str) -> None:
    from marianne.telemetry import ConsentRecord, ConsentState

    path = tmp_path / "consent.json"
    path.write_text(content)
    record = ConsentRecord(path)
    assert record.state is ConsentState.UNSET
    assert record.consent_id is None


def test_revoke_keeps_spool_unless_purge_requested(tmp_path: Path) -> None:
    from marianne.telemetry import ConsentRecord

    record = ConsentRecord(tmp_path / "consent.json")
    spool = tmp_path / "spool.jsonl"
    spool.write_text("staged data")
    record.revoke()
    assert spool.read_text() == "staged data"
    assert json.loads(record.path.read_text())["state"] == "revoked"


def test_failed_atomic_update_preserves_previous_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marianne.telemetry import ConsentRecord, ConsentState

    record = ConsentRecord(tmp_path / "consent.json")
    record.grant()
    original = record.path.read_bytes()

    def fail_replace(self: Path, target: Path) -> Path:
        raise OSError("simulated rename failure")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated rename failure"):
        record.revoke()
    assert record.path.read_bytes() == original
    assert record.state is ConsentState.GRANTED
    assert set(tmp_path.iterdir()) == {
        record.path,
        record.path.with_name(record.path.name + ".lock"),
    }


def test_nonpurging_revoke_waits_for_authorized_append(tmp_path, monkeypatch):
    from marianne.telemetry import ConsentRecord, ConsentState, TelemetryCollector
    from marianne.telemetry.spool import TelemetrySpool
    from tests.test_telemetry_collector import score

    consent = ConsentRecord(tmp_path / "consent.json")
    consent.grant()
    entered = threading.Event()
    release = threading.Event()
    revoked = threading.Event()
    original = TelemetrySpool._write

    def paused_write(self, records):
        entered.set()
        assert release.wait(5)
        original(self, records)

    def revoke():
        ConsentRecord(consent.path).revoke()
        revoked.set()

    monkeypatch.setattr(TelemetrySpool, "_write", paused_write)
    with ThreadPoolExecutor(max_workers=2) as pool:
        append = pool.submit(TelemetryCollector(consent).collect, score(tmp_path), "completed", [])
        assert entered.wait(5)
        revoke_future = pool.submit(revoke)
        returned_early = revoked.wait(0.1)
        release.set()
        assert append.result(timeout=5)
        revoke_future.result(timeout=5)
    assert not returned_early
    assert consent.state is ConsentState.REVOKED
    assert not TelemetryCollector(consent).collect(score(tmp_path), "completed", [])
