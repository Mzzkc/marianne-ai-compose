"""``mzt doctor`` runs REA's own doctor for each REA pool entry (GH #499).

REA (Reverse Engineer Anything) is one stdio MCP server the conductor's pool
can run like any other. Its toolchain (Ghidra release, Java, JADX, IDA) is
REA's business: ``rea doctor --json`` is read-only, exits 1 when a required
check fails, and names each gap. Marianne's doctor consumes that verbatim for
every pool entry whose command runs REA, and never installs or repairs.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from marianne.cli import app
from marianne.cli.commands.doctor import (
    _rea_doctor_command,
    _rea_pool_entries,
    _rea_provider_checks,
)

runner = CliRunner()

REA_COMMAND = (
    "/opt/node24/bin/node /opt/node24/lib/node_modules/rea-agents/scripts/rea.mjs mcp"
)


def _conductor_yaml(tmp_path: Path, servers: dict[str, dict[str, str]]) -> Path:
    path = tmp_path / "conductor.yaml"
    body = ["mcp_pool:", "  servers:"]
    for name, entry in servers.items():
        body.append(f"    {name}:")
        for key, value in entry.items():
            body.append(f'      {key}: "{value}"')
    path.write_text("\n".join(body) + "\n")
    return path


class TestPoolEntryDiscovery:
    def test_rea_entry_is_found_by_its_script(self, tmp_path: Path) -> None:
        cfg = _conductor_yaml(tmp_path, {
            "rea": {"command": REA_COMMAND, "socket": "/tmp/rea.sock"},
            "filesystem": {"command": "npx -y @modelcontextprotocol/server-filesystem /x",
                           "socket": "/tmp/fs.sock"},
        })
        assert _rea_pool_entries(cfg) == [("rea", REA_COMMAND)]

    def test_absent_or_broken_config_yields_nothing(self, tmp_path: Path) -> None:
        assert _rea_pool_entries(tmp_path / "missing.yaml") == []
        broken = tmp_path / "broken.yaml"
        broken.write_text("mcp_pool: [not: a: map\n")
        assert _rea_pool_entries(broken) == []

    def test_doctor_command_swaps_mcp_for_doctor_json(self) -> None:
        argv = _rea_doctor_command(REA_COMMAND)
        assert argv[-2:] == ["doctor", "--json"]
        assert argv[0] == "/opt/node24/bin/node" and "mcp" not in argv


def _fake_run(report: dict[str, object], returncode: int):  # type: ignore[no-untyped-def]
    def run(argv, **kwargs):  # type: ignore[no-untyped-def]
        return subprocess.CompletedProcess(argv, returncode, stdout=json.dumps(report), stderr="")
    return run


class TestProviderChecks:
    def test_unhealthy_report_names_the_failing_checks(self) -> None:
        report = {
            "healthy": False,
            "checks": [
                {"name": "node", "ok": True, "detail": "24.22.2"},
                {"name": "ghidra-version", "ok": False,
                 "detail": "12.0.4; accepted releases are 12.1.x"},
                {"name": "ghidra-java", "ok": False, "detail": "Command deadline elapsed."},
            ],
        }
        with patch("subprocess.run", _fake_run(report, 1)):
            rows = _rea_provider_checks([("rea", REA_COMMAND)])
        assert rows[0]["status"] == "warning"
        assert rows[0]["exit_code"] == 1
        assert "ghidra-version" in rows[0]["detail"] and "ghidra-java" in rows[0]["detail"]
        assert "node" not in rows[0]["detail"]

    def test_remediation_stands_in_for_a_missing_detail(self) -> None:
        report = {"healthy": False, "checks": [
            {"name": "hopper", "ok": False, "classification": "missing_analysis_engine",
             "remediation": "Run rea setup to install Hopper, or set HOPPER_LAUNCHER_PATH."},
        ]}
        with patch("subprocess.run", _fake_run(report, 1)):
            rows = _rea_provider_checks([("rea", REA_COMMAND)])
        assert rows[0]["checks"][0]["detail"].startswith("Run rea setup")
        assert rows[0]["checks"][0]["classification"] == "missing_analysis_engine"

    def test_healthy_report_is_ok(self) -> None:
        with patch("subprocess.run", _fake_run({"healthy": True, "checks": []}, 0)):
            rows = _rea_provider_checks([("rea", REA_COMMAND)])
        assert rows[0]["status"] == "ok" and rows[0]["healthy"] is True

    def test_missing_interpreter_is_unavailable_not_a_crash(self) -> None:
        def boom(argv, **kwargs):  # type: ignore[no-untyped-def]
            raise OSError("no such file")
        with patch("subprocess.run", boom):
            rows = _rea_provider_checks([("rea", REA_COMMAND)])
        assert rows[0]["status"] == "unavailable"
        assert "did not run" in rows[0]["detail"]

    def test_no_entries_runs_nothing(self) -> None:
        with patch("subprocess.run", side_effect=AssertionError("must not run")):
            assert _rea_provider_checks([]) == []


class TestDoctorOutput:
    def test_json_carries_provider_checks_and_counts_the_gap(self) -> None:
        report = {"healthy": False,
                  "checks": [{"name": "ghidra-version", "ok": False, "detail": "12.0.4"}]}
        with (
            patch("marianne.cli.commands.doctor._check_conductor_status",
                  return_value=("not running", None)),
            patch("marianne.cli.commands.doctor._rea_pool_entries",
                  return_value=[("rea", REA_COMMAND)]),
            patch("subprocess.run", _fake_run(report, 1)),
        ):
            result = runner.invoke(app, ["doctor", "--json"])
        data = json.loads(result.stdout)
        assert data["provider_checks"][0]["entry"] == "rea"
        assert data["provider_checks"][0]["status"] == "warning"

    def test_rich_output_shows_a_providers_section(self) -> None:
        report = {"healthy": False,
                  "checks": [{"name": "ghidra-version", "ok": False,
                              "detail": "12.0.4; accepted releases are 12.1.x"}]}
        with (
            patch("marianne.cli.commands.doctor._check_conductor_status",
                  return_value=("not running", None)),
            patch("marianne.cli.commands.doctor._rea_pool_entries",
                  return_value=[("rea", REA_COMMAND)]),
            patch("subprocess.run", _fake_run(report, 1)),
        ):
            result = runner.invoke(app, ["doctor"])
        assert "Providers:" in result.stdout
        assert "ghidra-version" in result.stdout
        assert "never installs" in result.stdout

    def test_without_rea_entries_doctor_is_unchanged(self) -> None:
        with (
            patch("marianne.cli.commands.doctor._check_conductor_status",
                  return_value=("not running", None)),
            patch("marianne.cli.commands.doctor._rea_pool_entries", return_value=[]),
        ):
            result = runner.invoke(app, ["doctor", "--json"])
        data = json.loads(result.stdout)
        assert data["provider_checks"] == []
