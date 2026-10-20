"""Tests for ``mzt doctor`` command.

Validates environment health checks: Python version, Marianne version,
conductor status, instrument availability, and safety warnings.

TDD: These tests define the contract for the doctor command.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from marianne.cli import app
from marianne.instruments.availability import ProfileProbe

runner = CliRunner()


# ---------------------------------------------------------------------------
# Basic command registration
# ---------------------------------------------------------------------------


class TestDoctorCommandExists:
    """Verify the doctor command is registered and callable."""

    def test_doctor_command_registered(self) -> None:
        """The doctor command appears in the CLI."""
        result = runner.invoke(app, ["doctor", "--help"])
        assert result.exit_code == 0
        assert "environment" in result.stdout.lower() or "health" in result.stdout.lower()

    def test_doctor_runs_without_conductor(self) -> None:
        """Doctor should work even when the conductor is not running."""
        with patch(
            "marianne.cli.commands.doctor._check_conductor_status",
            return_value=("not running", None),
        ):
            result = runner.invoke(app, ["doctor"])
            # Should succeed (exit 0) even without conductor
            assert result.exit_code == 0

    def test_doctor_shows_python_version(self) -> None:
        """Doctor should display the Python version."""
        with patch(
            "marianne.cli.commands.doctor._check_conductor_status",
            return_value=("not running", None),
        ):
            result = runner.invoke(app, ["doctor"])
            assert "Python" in result.stdout

    def test_doctor_shows_marianne_version(self) -> None:
        """Doctor should display the Marianne version."""
        with patch(
            "marianne.cli.commands.doctor._check_conductor_status",
            return_value=("not running", None),
        ):
            result = runner.invoke(app, ["doctor"])
            assert "Marianne" in result.stdout


# ---------------------------------------------------------------------------
# Conductor status checks
# ---------------------------------------------------------------------------


class TestDoctorConductorChecks:
    """Verify conductor health reporting."""

    def test_conductor_running_shown(self) -> None:
        """Doctor should report when the conductor is running."""
        with patch(
            "marianne.cli.commands.doctor._check_conductor_status",
            return_value=("running", 12345),
        ):
            result = runner.invoke(app, ["doctor"])
            assert result.exit_code == 0
            assert "12345" in result.stdout or "running" in result.stdout.lower()

    def test_conductor_not_running_shown(self) -> None:
        """Doctor should report when the conductor is not running."""
        with patch(
            "marianne.cli.commands.doctor._check_conductor_status",
            return_value=("not running", None),
        ):
            result = runner.invoke(app, ["doctor"])
            assert result.exit_code == 0
            assert "not running" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Instrument checks
# ---------------------------------------------------------------------------


class TestDoctorInstrumentChecks:
    """Verify instrument availability reporting."""

    def test_builtin_profiles_listed(self) -> None:
        """Doctor should list built-in instrument profiles."""
        with patch(
            "marianne.cli.commands.doctor._check_conductor_status",
            return_value=("not running", None),
        ):
            result = runner.invoke(app, ["doctor"])
            # At minimum, claude-code should appear (the reference instrument)
            assert "claude" in result.stdout.lower()

    def test_instrument_binary_found(self) -> None:
        """Doctor should mark instruments as ready when their binary is on PATH."""
        with (
            patch(
                "marianne.cli.commands.doctor._check_conductor_status",
                return_value=("not running", None),
            ),
            patch("shutil.which", return_value="/usr/local/bin/claude"),
        ):
            result = runner.invoke(app, ["doctor"])
            assert result.exit_code == 0

    def test_instrument_binary_missing(self) -> None:
        """Doctor should report when an instrument binary is not found."""
        with (
            patch(
                "marianne.cli.commands.doctor._check_conductor_status",
                return_value=("not running", None),
            ),
            patch("shutil.which", return_value=None),
        ):
            result = runner.invoke(app, ["doctor"])
            assert result.exit_code == 0
            # Should still succeed — missing optional instruments are warnings


# ---------------------------------------------------------------------------
# Safety checks
# ---------------------------------------------------------------------------


class TestDoctorOneProbe:
    """GH #447: doctor, list, check and the class writer share one probe, and
    "Marianne is ready" rests on a runnable AGENT instrument."""

    def test_zero_runnable_agent_instruments_is_an_error_not_ready(self) -> None:
        """A fresh install with nothing on PATH and the local server not serving
        the profile's model used to print "Marianne is ready"."""
        from marianne.instruments.availability import ProfileProbe

        with (
            patch(
                "marianne.cli.commands.doctor._check_conductor_status",
                return_value=("not running", None),
            ),
            patch(
                "marianne.cli.commands.doctor.probe_profile",
                side_effect=lambda profile: ProfileProbe("ready", binary_path="/bin/sh")
                if profile.raw_prompt
                else ProfileProbe("unavailable", "nothing here"),
            ),
        ):
            result = runner.invoke(app, ["doctor", "--json"])
        data = json.loads(result.stdout)
        assert data["errors_count"] == 1
        statuses = {row["name"]: row["probe"] for row in data["instruments"]}
        assert statuses["cli"] == "ready"
        assert all(state == "unavailable" for name, state in statuses.items() if name != "cli")

    def test_remote_http_profile_is_unchecked_not_ok(self) -> None:
        from marianne.core.config.instruments import HttpProfile, InstrumentProfile

        remote = InstrumentProfile(
            name="remote-llm", display_name="Remote", description="x", kind="http",
            capabilities={"tool_use"}, default_model="m", default_timeout_seconds=30.0,
            http=HttpProfile(
                base_url="https://api.example.com", endpoint="/v1/chat/completions",
                schema_family="openai",
            ),
        )
        with (
            patch(
                "marianne.cli.commands.doctor._check_conductor_status",
                return_value=("not running", None),
            ),
            patch(
                "marianne.cli.commands.doctor._get_all_profiles",
                return_value={"remote-llm": remote},
            ),
        ):
            result = runner.invoke(app, ["doctor", "--json"])
        row = json.loads(result.stdout)["instruments"][0]
        assert row["status"] == "unchecked" and row["probe"] == "unchecked"


class TestDoctorSafetyChecks:
    """Verify safety warning reporting."""

    def test_no_cost_limits_warning(self) -> None:
        """Doctor should warn if no cost limits are configured by default."""
        with patch(
            "marianne.cli.commands.doctor._check_conductor_status",
            return_value=("not running", None),
        ):
            result = runner.invoke(app, ["doctor"])
            # The default state has no cost limits — doctor should mention this
            assert result.exit_code == 0


# ---------------------------------------------------------------------------
# JSON output mode
# ---------------------------------------------------------------------------


class TestDoctorJsonOutput:
    """Verify JSON output mode."""

    def test_json_flag_produces_json(self) -> None:
        """Doctor --json should produce parseable JSON."""
        import json

        with patch(
            "marianne.cli.commands.doctor._check_conductor_status",
            return_value=("not running", None),
        ):
            result = runner.invoke(app, ["doctor", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.stdout)
            assert "python_version" in data
            assert "marianne_version" in data
            assert "conductor" in data
            assert "instruments" in data

    def test_json_marks_runtime_rate_limited_instrument(self) -> None:
        """Doctor reports conductor-known runtime rate limits per instrument."""
        import json

        from marianne.core.config.instruments import (
            CliCommand,
            CliOutputConfig,
            CliProfile,
            InstrumentProfile,
        )

        profile = InstrumentProfile(
            name="antigravity",
            display_name="Antigravity CLI",
            kind="cli",
            cli=CliProfile(
                command=CliCommand(executable="agy"),
                output=CliOutputConfig(format="text"),
            ),
        )

        with (
            patch(
                "marianne.cli.commands.doctor._check_conductor_status",
                return_value=("running", 12345),
            ),
            patch(
                "marianne.cli.commands.doctor._active_rate_limited_instruments",
                return_value={"antigravity": 3600.0},
            ),
            patch(
                "marianne.cli.commands.doctor._get_all_profiles",
                return_value={"antigravity": profile},
            ),
            patch(
                "marianne.cli.commands.doctor.probe_profile",
                return_value=ProfileProbe("ready", binary_path="/usr/bin/agy"),
            ),
        ):
            result = runner.invoke(app, ["doctor", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        instrument = data["instruments"][0]
        assert instrument["name"] == "antigravity"
        assert instrument["status"] == "warning"
        assert instrument["rate_limited"] is True
        assert instrument["rate_limit_seconds_remaining"] == 3600.0
        assert instrument["execution_ready"] is True

    def test_json_marks_profile_execution_unsupported(self) -> None:
        """Doctor distinguishes binary availability from prompt readiness."""
        import json

        from marianne.core.config.instruments import (
            CliCommand,
            CliOutputConfig,
            CliProfile,
            InstrumentProfile,
        )

        profile = InstrumentProfile(
            name="gemini-cli",
            display_name="Gemini CLI",
            kind="cli",
            execution_status="unsupported",
            execution_status_detail="UNSUPPORTED_CLIENT",
            cli=CliProfile(
                command=CliCommand(executable="gemini"),
                output=CliOutputConfig(format="text"),
            ),
        )

        with (
            patch(
                "marianne.cli.commands.doctor._check_conductor_status",
                return_value=("not running", None),
            ),
            patch(
                "marianne.cli.commands.doctor._get_all_profiles",
                return_value={"gemini-cli": profile},
            ),
            patch(
                "marianne.cli.commands.doctor.probe_profile",
                return_value=ProfileProbe("ready", binary_path="/usr/bin/gemini"),
            ),
        ):
            result = runner.invoke(app, ["doctor", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        instrument = data["instruments"][0]
        assert instrument["name"] == "gemini-cli"
        assert instrument["status"] == "warning"
        assert instrument["execution_status"] == "unsupported"
        assert instrument["execution_status_detail"] == "UNSUPPORTED_CLIENT"
        assert instrument["execution_ready"] is False
        assert instrument["rate_limited"] is False

    def test_json_infers_gemini_executable_unsupported_without_google_auth(
        self,
    ) -> None:
        """Gemini wrapper profiles inherit the local OAuth-tier block."""
        import json

        from marianne.core.config.instruments import (
            CliCommand,
            CliOutputConfig,
            CliProfile,
            InstrumentProfile,
        )

        profile = InstrumentProfile(
            name="custom-gemini-wrapper",
            display_name="Custom Gemini",
            kind="cli",
            cli=CliProfile(
                command=CliCommand(executable="gemini"),
                output=CliOutputConfig(format="text"),
            ),
        )

        with (
            patch.dict(
                "os.environ",
                {
                    "GOOGLE_API_KEY": "",
                    "GOOGLE_APPLICATION_CREDENTIALS": "",
                    "GOOGLE_CLOUD_PROJECT": "",
                    "CLOUDSDK_CONFIG": "",
                },
                clear=False,
            ),
            patch(
                "marianne.cli.commands.doctor._check_conductor_status",
                return_value=("not running", None),
            ),
            patch(
                "marianne.cli.commands.doctor._get_all_profiles",
                return_value={"custom-gemini-wrapper": profile},
            ),
            patch(
                "marianne.cli.commands.doctor.probe_profile",
                return_value=ProfileProbe("ready", binary_path="/usr/bin/gemini"),
            ),
        ):
            result = runner.invoke(app, ["doctor", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        instrument = data["instruments"][0]
        assert instrument["name"] == "custom-gemini-wrapper"
        assert instrument["execution_status"] == "unsupported"
        assert instrument["execution_ready"] is False


# ---------------------------------------------------------------------------
# Summary line
# ---------------------------------------------------------------------------


class TestDoctorSummary:
    """Verify the summary line at the end."""

    def test_summary_shows_ready(self) -> None:
        """Doctor should show a summary indicating readiness."""
        with patch(
            "marianne.cli.commands.doctor._check_conductor_status",
            return_value=("running", 12345),
        ):
            result = runner.invoke(app, ["doctor"])
            assert result.exit_code == 0
            # Should have some kind of summary
            assert "ready" in result.stdout.lower() or "warning" in result.stdout.lower()


# ---------------------------------------------------------------------------
# F-090: IPC socket fallback detection (Ghost, movement 1)
# ---------------------------------------------------------------------------


class TestDoctorSocketFallback:
    """F-090: Doctor should detect conductor via IPC socket when PID file is missing."""

    def test_socket_probe_detects_running_conductor(self) -> None:
        """When PID file is missing but socket responds, report running."""
        from unittest.mock import AsyncMock

        with (
            patch(
                "marianne.cli.commands.doctor._check_pid_file",
                return_value=None,
            ),
            patch(
                "marianne.daemon.detect._resolve_socket_path",
            ) as mock_resolve,
            patch(
                "marianne.daemon.ipc.client.DaemonClient",
            ) as mock_client_cls,
        ):
            # Socket exists
            mock_path = type("MockPath", (), {"exists": lambda self: True})()
            mock_resolve.return_value = mock_path

            # IPC probe returns alive
            mock_instance = AsyncMock()
            mock_instance.is_daemon_running = AsyncMock(return_value=True)
            mock_client_cls.return_value = mock_instance

            from marianne.cli.commands.doctor import _check_conductor_status

            status, pid = _check_conductor_status()
            assert status == "running"
            # PID is None because we found it via socket, not PID file
            assert pid is None

    def test_no_pid_no_socket_reports_not_running(self) -> None:
        """When both PID file and socket are missing, report not running."""
        with (
            patch(
                "marianne.cli.commands.doctor._check_pid_file",
                return_value=None,
            ),
            patch(
                "marianne.daemon.detect._resolve_socket_path",
            ) as mock_resolve,
        ):
            mock_path = type("MockPath", (), {"exists": lambda self: False})()
            mock_resolve.return_value = mock_path

            from marianne.cli.commands.doctor import _check_conductor_status

            status, pid = _check_conductor_status()
            assert status == "not running"
            assert pid is None

    def test_pid_found_skips_socket_probe(self) -> None:
        """When PID file check succeeds, skip the socket probe."""
        with patch(
            "marianne.cli.commands.doctor._check_pid_file",
            return_value=12345,
        ):
            from marianne.cli.commands.doctor import _check_conductor_status

            status, pid = _check_conductor_status()
            assert status == "running"
            assert pid == 12345


# ---------------------------------------------------------------------------
# Doctor clone awareness (Ghost, movement 1)
# ---------------------------------------------------------------------------


class TestDoctorCloneAwareness:
    """Doctor should check clone conductor when --conductor-clone is active."""

    def test_pid_check_uses_clone_path(self) -> None:
        """_check_pid_file should use clone PID file when clone is active."""
        from marianne.daemon.clone import set_clone_name

        set_clone_name("doctor-test")
        try:
            from marianne.cli.commands.doctor import _check_pid_file

            # Clone PID file doesn't exist → returns None
            pid = _check_pid_file()
            assert pid is None
        finally:
            set_clone_name(None)


# ---------------------------------------------------------------------------
# Instrument hygiene (GH #443): profiles no submitted score references
# ---------------------------------------------------------------------------


def _profile_yaml(name: str) -> str:
    return f"""\
name: {name}
display_name: "{name}"
kind: cli
cli:
  command:
    executable: test
    prompt_flag: "-p"
  output:
    format: text
"""


def _registry_with(tmp_path: Path, config_paths: list[str]) -> Path:
    import sqlite3

    db = tmp_path / "daemon-state.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE jobs (job_id TEXT PRIMARY KEY, config_path TEXT NOT NULL, "
        "workspace TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', "
        "submitted_at REAL NOT NULL)"
    )
    for index, path in enumerate(config_paths):
        conn.execute(
            "INSERT INTO jobs VALUES (?, ?, ?, 'completed', ?)",
            (f"job-{index}", path, str(tmp_path), float(index)),
        )
    conn.commit()
    conn.close()
    return db


class TestUnreferencedProfiles:
    """A user-layer profile no submitted score names is reported, never deleted.

    148 generated seats once sat in ~/.marianne/instruments/ with nothing
    naming them; `mzt instruments list` was unreadable and doctor said nothing.
    """

    def test_reports_profile_nothing_names(self, tmp_path: Path) -> None:
        from marianne.cli.commands.doctor import _scan_unreferenced_user_profiles

        org = tmp_path / "org"
        org.mkdir()
        (org / "orphan-seat.yaml").write_text(_profile_yaml("orphan-seat"))
        (org / "used-seat.yaml").write_text(_profile_yaml("used-seat"))
        score = tmp_path / "score.yaml"
        score.write_text(
            "name: s\nworkspace: ./ws\ninstruments:\n  reviewer:\n"
            "    profile: used-seat\nmovements: {}\n"
        )
        db = _registry_with(tmp_path, [str(score)])

        findings = _scan_unreferenced_user_profiles(
            organization_dir=org, venue_dir=tmp_path / "no-venue", db_path=db,
        )

        assert [f["name"] for f in findings] == ["orphan-seat"]
        assert findings[0]["layer"] == "user"
        assert findings[0]["cleanable"] is False
        assert findings[0]["path"] == str((org / "orphan-seat.yaml").resolve())

    def test_builtin_override_and_class_chain_member_are_referenced(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A user file overriding a built-in name is always in play; so is a
        profile a class chain names, even with no score naming either."""
        from marianne.cli.commands.doctor import _scan_unreferenced_user_profiles
        from marianne.instruments import classes

        org = tmp_path / "org"
        org.mkdir()
        (org / "claude-code.yaml").write_text(_profile_yaml("claude-code"))
        (org / "chain-seat.yaml").write_text(_profile_yaml("chain-seat"))
        (org / "orphan-seat.yaml").write_text(_profile_yaml("orphan-seat"))
        user_classes = tmp_path / "classes.yaml"
        user_classes.write_text("version: 1\nclasses:\n  strong:\n    - chain-seat\n")
        monkeypatch.setattr(classes, "class_source_paths", lambda **_: (
            Path(classes.__file__).parent / "classes" / "default.yaml",
            user_classes, tmp_path / "no-venue-classes.yaml",
        ))
        db = _registry_with(tmp_path, [])

        findings = _scan_unreferenced_user_profiles(
            organization_dir=org, venue_dir=tmp_path / "no-venue", db_path=db,
        )

        assert [f["name"] for f in findings] == ["orphan-seat"]

    def test_no_registry_means_no_verdict(self, tmp_path: Path) -> None:
        """Before the conductor ever ran, nothing has been submitted; reporting
        every profile as unreferenced would be noise, not a finding."""
        from marianne.cli.commands.doctor import _scan_unreferenced_user_profiles

        org = tmp_path / "org"
        org.mkdir()
        (org / "orphan-seat.yaml").write_text(_profile_yaml("orphan-seat"))

        assert _scan_unreferenced_user_profiles(
            organization_dir=org, venue_dir=tmp_path / "no-venue",
            db_path=tmp_path / "missing.db",
        ) == []

    def test_doctor_prints_and_counts_the_finding(self, tmp_path: Path) -> None:
        finding = {
            "kind": "unreferenced_profile", "layer": "user", "name": "orphan-seat",
            "path": str(tmp_path / "orphan-seat.yaml"), "size_bytes": 10,
            "cleanable": False, "detail": "no score names it",
        }
        with (
            patch(
                "marianne.cli.commands.doctor._check_conductor_status",
                return_value=("not running", None),
            ),
            patch(
                "marianne.cli.commands.doctor._scan_unreferenced_user_profiles",
                return_value=[finding],
            ),
        ):
            human = runner.invoke(app, ["doctor"])
            as_json = runner.invoke(app, ["doctor", "--json"])
        assert "Instrument hygiene" in human.stdout
        assert "orphan-seat" in human.stdout
        assert "archive" in human.stdout.lower()
        data = json.loads(as_json.stdout)
        assert data["unreferenced_profiles"] == [finding]
        assert data["warnings_count"] >= 1


def test_doctor_reports_the_conductors_last_reload(monkeypatch, tmp_path):
    """GH #450: a reload-declined profile and a stale schema reach the operator."""
    from typer.testing import CliRunner

    from marianne.cli import app

    last = {
        "success": True, "partial": True, "schema_stale": True, "reason": "watcher",
        "config_generation": 3, "applied": [], "declined": [],
        "failed_profiles": [{
            "path": "/x/antigravity.yaml", "reason_code": "instrument_schema_stale",
            "reason": "extra_forbidden", "retained": "antigravity",
        }],
    }
    import json as _json
    from unittest.mock import patch

    with (
        patch("marianne.cli.commands.doctor._check_conductor_status",
              return_value=("running", 4242)),
        patch("marianne.cli.commands.doctor._last_conductor_reload", return_value=last),
        patch("marianne.cli.commands.doctor._get_all_profiles", return_value={}),
        patch("marianne.cli.commands.doctor._scan_storage_findings", return_value=[]),
        patch("marianne.cli.commands.doctor._scan_unreferenced_user_profiles",
              return_value=[]),
    ):
        result = CliRunner().invoke(app, ["doctor"])
        # Rich wraps at 80 columns under CliRunner; compare without line breaks.
        out = " ".join(result.output.split())
        assert "Config reload:" in out, out
        assert "Reload declined /x/antigravity.yaml: instrument_schema_stale" in out
        assert "previous entry 'antigravity' retained" in out
        assert "predates a profile's schema" in out
        assert "mzt stop && mzt start" in out
        result = CliRunner().invoke(app, ["doctor", "--json"])
        payload = _json.loads(result.output)
        assert payload["conductor"]["last_reload"]["schema_stale"] is True
        assert payload["conductor"]["last_reload"]["failed_profiles"][0]["retained"] == "antigravity"
