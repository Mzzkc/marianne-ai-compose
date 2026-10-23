"""The public telemetry commands operate on isolated user-global state."""

import json
import stat

import pytest
from typer.testing import CliRunner

from marianne.cli import app
from marianne.telemetry import ConsentRecord
from marianne.telemetry.spool import TelemetrySpool

runner = CliRunner()


@pytest.fixture(autouse=True)
def private_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))


def invoke(*args):
    return runner.invoke(app, ["telemetry", *args])


def test_help_and_status_default_to_no_permission(tmp_path):
    result = invoke("--help")
    assert result.exit_code == 0
    assert all(command in result.output for command in ("status", "consent", "export", "flush"))
    result = invoke("status")
    assert result.exit_code == 0
    assert "unset" in result.output
    assert "0" in result.output
    assert not (tmp_path / ".marianne/telemetry-consent.json").exists()


def test_explicit_consent_and_optional_purge():
    assert invoke("consent", "--grant").exit_code == 0
    assert ConsentRecord().consent_id
    spool = TelemetrySpool()
    spool.append({"example": 1})
    assert invoke("consent", "--revoke").exit_code == 0
    assert ConsentRecord().consent_id is None
    assert spool.count() == 1
    assert invoke("consent", "--purge").exit_code == 0
    assert spool.count() == 0


@pytest.mark.parametrize("flags", [[], ["--grant", "--revoke"], ["--grant", "--purge"]])
def test_invalid_consent_flags_do_not_change_permission(flags):
    assert invoke("consent", *flags).exit_code != 0
    assert ConsentRecord().consent_id is None


def test_export_jsonl_to_stdout_and_private_file(tmp_path):
    spool = TelemetrySpool()
    spool.append({"schema_version": 1, "payload": {"example": 2}})
    result = invoke("export")
    assert result.exit_code == 0
    assert json.loads(result.output) == spool.read()[0]
    destination = tmp_path / "export.jsonl"
    destination.write_text("old")
    destination.chmod(0o644)
    result = invoke("export", "--out", str(destination))
    assert result.exit_code == 0
    assert json.loads(destination.read_text()) == spool.read()[0]
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert spool.count() == 1


def test_custom_spool_status_export_and_revoke_purge(tmp_path):
    path = tmp_path / "custom.jsonl"
    TelemetrySpool(path).append({"custom": True})
    assert "1" in invoke("status", "--spool-path", str(path)).output
    assert json.loads(invoke("export", "--spool-path", str(path)).output) == {"custom": True}
    assert invoke("consent", "--revoke", "--purge", "--spool-path", str(path)).exit_code == 0
    assert not path.exists()


def test_default_flush_and_dry_run_preserve_spool(monkeypatch):
    from marianne.telemetry.transmitter import TelemetryTransmitter

    async def forbid(*args, **kwargs):
        pytest.fail("unexpected HTTP request")

    monkeypatch.setattr(TelemetryTransmitter, "_post", forbid)
    assert invoke("consent", "--grant").exit_code == 0
    TelemetrySpool().append({"consent_id": ConsentRecord().consent_id})
    assert invoke("flush").exit_code == 0
    assert (
        invoke("flush", "--push", "--endpoint", "https://example.test", "--dry-run").exit_code == 0
    )
    assert TelemetrySpool().count() == 1


def test_explicit_push_and_transport_failure(monkeypatch):
    from marianne.telemetry.transmitter import TelemetryTransmitter

    sent = []

    async def post(self, envelopes):
        sent.extend(envelopes)
        return True

    monkeypatch.setattr(TelemetryTransmitter, "_post", post)
    invoke("consent", "--grant")
    envelope = {"consent_id": ConsentRecord().consent_id}
    TelemetrySpool().append(envelope)
    assert invoke("flush", "--push").exit_code != 0
    assert invoke("flush", "--push", "--endpoint", "https://example.test").exit_code == 0
    assert sent == [envelope]
    assert TelemetrySpool().count() == 0


def test_storage_errors_report_failure_without_sensitive_details(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("private /home/alice secret")

    monkeypatch.setattr(ConsentRecord, "grant", fail)
    result = invoke("consent", "--grant")
    assert result.exit_code == 1
    assert "failed" in result.output.lower()
    assert "alice" not in result.output
    TelemetrySpool().path.parent.mkdir(parents=True, exist_ok=True)
    TelemetrySpool().path.write_text("broken json")
    for command in ("status", "export"):
        assert invoke(command).exit_code == 1


def test_export_cannot_replace_consent_or_spool_control_files():
    invoke("consent", "--grant")
    spool = TelemetrySpool()
    spool.append({"consent_id": ConsentRecord().consent_id})
    before = ConsentRecord().path.read_bytes()
    for path in (ConsentRecord().path, spool.path, spool.path.with_name(spool.path.name + ".lock")):
        assert invoke("export", "--out", str(path)).exit_code != 0
    assert ConsentRecord().path.read_bytes() == before
    assert spool.count() == 1


def test_export_cannot_replace_another_spool_lock(tmp_path):
    another = TelemetrySpool(tmp_path / "another.jsonl")
    another.append({"first": 1})
    lock = another.path.with_name(another.path.name + ".lock")
    before = lock.stat().st_ino
    assert invoke("export", "--out", str(lock)).exit_code != 0
    assert lock.stat().st_ino == before
