"""Local collection preserves outcomes while refusing unconsented staging."""

import json
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from marianne.core.config import JobConfig
from marianne.telemetry import ConsentRecord


def score(tmp_path: Path, enabled: bool = True) -> JobConfig:
    return JobConfig.model_validate({
        "name": "example", "instrument": "ollama", "workspace": str(tmp_path),
        "sheet": {"size": 1, "total_items": 1}, "prompt": {"template": "fictional"},
        "telemetry": {"enabled": enabled, "spool_path": str(tmp_path / "spool.jsonl")},
    })


def test_envelope_redacts_score_and_nested_results(tmp_path):
    from marianne.telemetry import TelemetryCollector
    from marianne.telemetry.spool import TelemetrySpool

    config = score(tmp_path)
    consent = ConsentRecord(tmp_path / "consent.json")
    consent.grant()
    results = [{"status": "failed", "attempt_count": 3, "outcome_data": {
        "error": "file /home/alice/private.txt TOKEN=fictional-token"},
        "validation_passed": False, "learned_patterns": ["retry"]}]
    assert TelemetryCollector(consent).collect(config, "failed", results)
    spool = TelemetrySpool(config.telemetry.spool_path)
    envelope, = spool.read()
    assert envelope["schema_version"] == 1
    assert envelope["consent_id"] == consent.consent_id
    assert envelope["collected_at"]
    assert envelope["payload"]["job"] == {"status": "failed", "sheet_count": 1}
    assert envelope["payload"]["sheets"][0]["attempt_count"] == 3
    serialized = json.dumps(envelope)
    assert "/home/alice" not in serialized
    assert "fictional-token" not in serialized
    assert str(tmp_path) not in serialized
    assert "telemetry" not in envelope["payload"]["score"]
    assert results[0]["outcome_data"]["error"].startswith("file /home/alice")
    assert stat.S_IMODE(spool.path.stat().st_mode) == 0o600


@pytest.mark.parametrize("enabled,grant", [(False, False), (False, True), (True, False)])
def test_refusal_creates_no_spool(tmp_path, enabled, grant):
    from marianne.telemetry import TelemetryCollector

    consent = ConsentRecord(tmp_path / "consent.json")
    if grant:
        consent.grant()
    config = score(tmp_path, enabled)
    assert not TelemetryCollector(consent).collect(config, "completed", [])
    assert not config.telemetry.spool_path.exists()


def test_collector_observes_revocation(tmp_path):
    from marianne.telemetry import TelemetryCollector
    from marianne.telemetry.spool import TelemetrySpool

    consent = ConsentRecord(tmp_path / "consent.json")
    consent.grant()
    collector = TelemetryCollector(consent)
    config = score(tmp_path)
    assert collector.collect(config, "completed", [])
    ConsentRecord(consent.path).revoke()
    assert not collector.collect(config, "failed", [])
    assert TelemetrySpool(config.telemetry.spool_path).count() == 1


def test_concurrent_spool_append_and_purge(tmp_path):
    from marianne.telemetry.spool import TelemetrySpool

    path = tmp_path / "private" / "spool.jsonl"
    spool = TelemetrySpool(path)
    assert spool.read() == []
    assert spool.count() == 0
    assert not path.parent.exists()
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda n: TelemetrySpool(path).append({"n": n}), range(40)))
    assert spool.count() == 40
    assert {item["n"] for item in spool.read()} == set(range(40))
    spool.purge()
    assert spool.count() == 0
    assert not path.exists()
    spool.purge()


def test_failed_spool_replace_keeps_previous_entries(tmp_path, monkeypatch):
    from marianne.telemetry.spool import TelemetrySpool

    spool = TelemetrySpool(tmp_path / "spool.jsonl")
    spool.append({"n": 1})
    before = spool.path.read_bytes()

    def fail(self, target):
        raise OSError("fixture rename failed")

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError, match="fixture rename failed"):
        spool.append({"n": 2})
    assert spool.path.read_bytes() == before
    assert not list(tmp_path.glob(".spool.jsonl.*"))


def test_malformed_spool_is_preserved(tmp_path):
    from marianne.telemetry.spool import TelemetrySpool

    spool = TelemetrySpool(tmp_path / "spool.jsonl")
    spool.path.write_text("broken json\n")
    with pytest.raises(ValueError):
        spool.append({"n": 2})
    assert spool.path.read_text() == "broken json\n"


def test_consent_purge_uses_spool_protocol(tmp_path):
    from marianne.telemetry.spool import TelemetrySpool

    spool = TelemetrySpool(tmp_path / "spool.jsonl")
    spool.append({"n": 1})
    consent = ConsentRecord(tmp_path / "consent.json")
    consent.grant()
    consent.revoke(spool_path=spool.path, purge=True)
    assert spool.count() == 0


def test_revocation_during_sanitization_refuses_append(tmp_path, monkeypatch):
    from marianne.telemetry import TelemetryCollector
    from marianne.telemetry import collector as module

    consent = ConsentRecord(tmp_path / "consent.json")
    consent.grant()
    config = score(tmp_path)
    original = module.sanitize_score

    def revoke_while_building(value):
        consent.revoke()
        return original(value)

    monkeypatch.setattr(module, "sanitize_score", revoke_while_building)
    assert not TelemetryCollector(consent).collect(config, "completed", [])
    assert not config.telemetry.spool_path.exists()


def test_spool_refuses_symlink_without_changing_target(tmp_path):
    from marianne.telemetry.spool import TelemetrySpool

    target = tmp_path / "private.jsonl"
    target.write_text('{"private": "retained"}\n')
    link = tmp_path / "spool.jsonl"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="symbolic link"):
        TelemetrySpool(link).append({"n": 1})
    assert target.read_text() == '{"private": "retained"}\n'
    assert link.is_symlink()


def test_spool_rejects_nonobject_without_overwriting(tmp_path):
    from marianne.telemetry.spool import TelemetrySpool

    spool = TelemetrySpool(tmp_path / "spool.jsonl")
    spool.path.write_text('42\n')
    with pytest.raises(ValueError, match="objects"):
        spool.append({"n": 1})
    assert spool.path.read_text() == '42\n'


def test_nonserializable_append_preserves_spool(tmp_path):
    from marianne.telemetry.spool import TelemetrySpool

    spool = TelemetrySpool(tmp_path / "spool.jsonl")
    spool.append({"n": 1})
    with pytest.raises(TypeError):
        spool.append({"n": object()})
    assert spool.read() == [{"n": 1}]
    assert not list(tmp_path.glob(".spool.jsonl.*"))


def test_purge_waits_for_first_append_before_returning(tmp_path):
    """A missing spool does not mean no authorized writer is in flight."""
    from threading import Event

    from marianne.telemetry.spool import TelemetrySpool

    spool = TelemetrySpool(tmp_path / "spool.jsonl")
    authorized = Event()
    release_writer = Event()
    purged = Event()

    def authorize():
        authorized.set()
        assert release_writer.wait(5)
        return True

    def purge():
        spool.purge()
        purged.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(spool.append, {"n": 1}, authorize=authorize)
        try:
            assert authorized.wait(5)
            assert not spool.path.exists()
            purger = pool.submit(purge)
            # The first writer holds the lock. A purger must not return yet.
            returned_early = purged.wait(0.2)
        finally:
            release_writer.set()
        assert writer.result(timeout=5)
        purger.result(timeout=5)
    assert not returned_early
    assert not spool.path.exists()


@pytest.mark.parametrize("suffix", ["", ".lock"])
def test_spool_refuses_consent_control_path_collision(tmp_path, suffix):
    from marianne.telemetry.spool import TelemetrySpool

    consent = ConsentRecord(tmp_path / "consent.json")
    consent.grant()
    before = consent.path.read_bytes()
    with pytest.raises(ValueError, match="consent"):
        TelemetrySpool(Path(str(consent.path) + suffix), consent_path=consent.path)
    assert consent.path.read_bytes() == before


@pytest.mark.parametrize("suffix", [".lock", ".submit.lock"])
def test_spool_cannot_replace_another_spool_lock(tmp_path, suffix):
    from marianne.telemetry.spool import TelemetrySpool

    spool = TelemetrySpool(tmp_path / "spool.jsonl")
    spool.append({"first": 1})
    with spool.submission() as acquired:
        assert acquired
    lock = spool.path.with_name(spool.path.name + suffix)
    before = lock.stat().st_ino
    with pytest.raises(ValueError, match="lock"):
        TelemetrySpool(lock).append({"other": 2})
    assert lock.stat().st_ino == before
    assert spool.read() == [{"first": 1}]


def test_spool_alias_cannot_replace_lock(tmp_path):
    from marianne.telemetry.spool import TelemetrySpool

    lock = tmp_path / "spool.jsonl.lock"
    lock.touch()
    alias = tmp_path / "alias.jsonl"
    alias.symlink_to(lock)
    with pytest.raises(ValueError, match="lock"):
        TelemetrySpool(alias)
    assert alias.is_symlink()
