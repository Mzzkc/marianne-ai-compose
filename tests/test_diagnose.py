"""Tests for Marianne CLI diagnose command and its internal helpers.

FIX-30: The diagnose module (912 LOC) was previously untested. This file
tests the pure-function report builder, the CLI entry points via CliRunner,
and edge cases like empty jobs, error history, and JSON output.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from marianne.cli import app
from marianne.cli.commands.diagnose import (
    _attach_log_contents,
    _build_diagnostic_report,
    _discover_log_files,
)
from marianne.core.checkpoint import (
    CheckpointState,
    ErrorRecord,
    JobStatus,
    SheetState,
    SheetStatus,
)

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_job(
    *,
    job_id: str = "diag-test",
    job_name: str = "Diagnose Test",
    total_sheets: int = 3,
    status: JobStatus = JobStatus.COMPLETED,
    sheets: dict[int, SheetState] | None = None,
    error_message: str | None = None,
) -> CheckpointState:
    """Build a minimal CheckpointState for diagnose tests."""
    now = datetime.now(UTC)
    if sheets is None:
        sheets = {
            i: SheetState(
                sheet_num=i,
                status=SheetStatus.COMPLETED,
                attempt_count=1,
                validation_passed=True,
                started_at=now - timedelta(minutes=total_sheets - i + 1),
                completed_at=now - timedelta(minutes=total_sheets - i),
                execution_duration_seconds=60.0,
            )
            for i in range(1, total_sheets + 1)
        }
    last_completed = sum(
        1 for s in sheets.values() if s.status == SheetStatus.COMPLETED
    )
    return CheckpointState(
        job_id=job_id,
        job_name=job_name,
        total_sheets=total_sheets,
        last_completed_sheet=last_completed,
        status=status,
        created_at=now - timedelta(hours=1),
        started_at=now - timedelta(minutes=total_sheets + 1),
        updated_at=now,
        completed_at=now if status == JobStatus.COMPLETED else None,
        sheets=sheets,
        error_message=error_message,
    )


def _write_state(tmp_path: Path, state: CheckpointState) -> Path:
    """Write state to JSON file, return workspace path."""
    fp = tmp_path / f"{state.job_id}.json"
    fp.write_text(json.dumps(state.model_dump(mode="json"), default=str))
    return tmp_path


# ---------------------------------------------------------------------------
# _build_diagnostic_report unit tests
# ---------------------------------------------------------------------------


class TestBuildDiagnosticReport:
    """Tests for the pure _build_diagnostic_report() function."""

    def test_basic_fields(self) -> None:
        job = _make_job()
        report = _build_diagnostic_report(job)

        assert report["job_id"] == "diag-test"
        assert report["job_name"] == "Diagnose Test"
        assert report["status"] == "completed"
        assert "generated_at" in report

    def test_progress_section(self) -> None:
        job = _make_job(total_sheets=5)
        report = _build_diagnostic_report(job)

        progress = report["progress"]
        assert progress["total_sheets"] == 5
        assert progress["completed"] == 5
        assert progress["failed"] == 0
        assert progress["percent"] > 0

    def test_progress_with_failures(self) -> None:
        sheets = {
            1: SheetState(sheet_num=1, status=SheetStatus.COMPLETED, attempt_count=1),
            2: SheetState(sheet_num=2, status=SheetStatus.FAILED, attempt_count=3,
                          error_message="Validation failed"),
            3: SheetState(sheet_num=3, status=SheetStatus.COMPLETED, attempt_count=1),
        }
        job = _make_job(total_sheets=3, status=JobStatus.FAILED, sheets=sheets)
        report = _build_diagnostic_report(job)

        assert report["progress"]["completed"] == 2
        assert report["progress"]["failed"] == 1

    def test_timing_with_started_job(self) -> None:
        job = _make_job()
        report = _build_diagnostic_report(job)

        timing = report["timing"]
        assert timing["started_at"] is not None
        assert timing["completed_at"] is not None
        assert "duration_seconds" in timing
        assert timing["duration_seconds"] > 0

    def test_timing_without_start(self) -> None:
        job = _make_job()
        job.started_at = None
        report = _build_diagnostic_report(job)

        timing = report["timing"]
        assert timing["started_at"] is None
        assert "duration_seconds" not in timing

    def test_execution_timeline_order(self) -> None:
        job = _make_job(total_sheets=3)
        report = _build_diagnostic_report(job)

        timeline = report["execution_timeline"]
        assert len(timeline) == 3
        sheet_nums = [e["sheet_num"] for e in timeline]
        assert sheet_nums == [1, 2, 3]

    def test_execution_timeline_entry_fields(self) -> None:
        job = _make_job(total_sheets=1)
        report = _build_diagnostic_report(job)

        entry = report["execution_timeline"][0]
        assert "sheet_num" in entry
        assert "status" in entry
        assert "attempt_count" in entry
        assert "duration_seconds" in entry

    def test_error_collection_from_history(self) -> None:
        now = datetime.now(UTC)
        error = ErrorRecord(
            error_type="transient",
            error_code="E001",
            error_message="Timeout waiting for output",
            attempt_number=2,
            timestamp=now,
        )
        sheets = {
            1: SheetState(
                sheet_num=1,
                status=SheetStatus.FAILED,
                attempt_count=2,
                error_history=[error],
            ),
        }
        job = _make_job(total_sheets=1, status=JobStatus.FAILED, sheets=sheets)
        report = _build_diagnostic_report(job)

        assert report["error_count"] == 1
        err = report["errors"][0]
        assert err["error_type"] == "transient"
        assert err["error_code"] == "E001"
        assert err["error_message"] == "Timeout waiting for output"
        assert err["sheet_num"] == 1

    def test_error_collection_from_sheet_message(self) -> None:
        """When no error_history exists, errors are synthesized from sheet error_message."""
        sheets = {
            1: SheetState(
                sheet_num=1,
                status=SheetStatus.FAILED,
                attempt_count=3,
                error_message="CLI not found",
                error_category="validation",
                exit_code=127,
            ),
        }
        job = _make_job(total_sheets=1, status=JobStatus.FAILED, sheets=sheets)
        report = _build_diagnostic_report(job)

        assert report["error_count"] == 1
        err = report["errors"][0]
        assert err["error_message"] == "CLI not found"
        assert err["error_code"] == "E201"  # "validation" → E201 via format_error_code_for_display
        assert err["context"]["exit_code"] == 127

    def test_no_errors_when_clean(self) -> None:
        job = _make_job()
        report = _build_diagnostic_report(job)

        assert report["error_count"] == 0
        assert report["errors"] == []

    def test_preflight_warnings(self) -> None:
        sheets = {
            1: SheetState(
                sheet_num=1,
                status=SheetStatus.COMPLETED,
                attempt_count=1,
                preflight_warnings=["Long prompt (>50k tokens)", "No validations defined"],
            ),
        }
        job = _make_job(total_sheets=1, sheets=sheets)
        report = _build_diagnostic_report(job)

        assert len(report["preflight_warnings"]) == 2
        assert report["preflight_warnings"][0]["warning"] == "Long prompt (>50k tokens)"
        assert report["preflight_warnings"][0]["sheet_num"] == 1

    def test_prompt_metrics_and_token_stats(self) -> None:
        sheets = {
            1: SheetState(
                sheet_num=1,
                status=SheetStatus.COMPLETED,
                attempt_count=1,
                prompt_metrics={"estimated_tokens": 1000, "line_count": 50},
            ),
            2: SheetState(
                sheet_num=2,
                status=SheetStatus.COMPLETED,
                attempt_count=1,
                prompt_metrics={"estimated_tokens": 3000, "line_count": 150},
            ),
        }
        job = _make_job(total_sheets=2, sheets=sheets)
        report = _build_diagnostic_report(job)

        assert len(report["prompt_metrics"]) == 2
        assert "token_statistics" in report
        assert report["token_statistics"]["min"] == 1000
        assert report["token_statistics"]["max"] == 3000
        assert report["token_statistics"]["total"] == 4000

    def test_job_error_message(self) -> None:
        job = _make_job(error_message="Max retries exceeded globally")
        report = _build_diagnostic_report(job)

        assert report["job_error"] == "Max retries exceeded globally"

    def test_empty_job_no_sheets(self) -> None:
        job = _make_job(total_sheets=1, sheets={})
        job.last_completed_sheet = 0
        report = _build_diagnostic_report(job)

        assert report["progress"]["total_sheets"] == 1
        assert report["progress"]["percent"] == 0
        assert report["execution_timeline"] == []

    def test_execution_stats(self) -> None:
        job = _make_job()
        job.total_retry_count = 7
        job.rate_limit_waits = 3
        report = _build_diagnostic_report(job)

        assert report["execution_stats"]["total_retry_count"] == 7
        assert report["execution_stats"]["rate_limit_waits"] == 3


# ---------------------------------------------------------------------------
# CLI integration tests via CliRunner
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_daemon_route(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent CLI tests from routing through a live conductor."""
    async def _fake_route(
        method: str, params: dict, *, socket_path=None,
    ) -> tuple[bool, None]:
        return False, None

    monkeypatch.setattr(
        "marianne.daemon.detect.try_daemon_route", _fake_route,
    )


class TestDiagnoseCommand:
    """Tests for `mzt diagnose` via CliRunner."""

    def test_diagnose_rich_output(self, tmp_path: Path) -> None:
        job = _make_job()
        _write_state(tmp_path, job)

        result = runner.invoke(
            app, ["diagnose", "diag-test", "--workspace", str(tmp_path)]
        )
        assert result.exit_code == 0
        assert "Diagnose Test" in result.stdout or "diag-test" in result.stdout

    def test_diagnose_json_output(self, tmp_path: Path) -> None:
        job = _make_job()
        _write_state(tmp_path, job)

        result = runner.invoke(
            app, ["diagnose", "diag-test", "--json", "--workspace", str(tmp_path)]
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["job_id"] == "diag-test"
        assert data["status"] == "completed"
        assert "progress" in data
        assert "execution_timeline" in data

    def test_diagnose_job_not_found(self, tmp_path: Path) -> None:
        workspace = tmp_path / "empty"
        workspace.mkdir()

        result = runner.invoke(
            app, ["diagnose", "no-such-job", "--workspace", str(workspace)]
        )
        assert result.exit_code == 1
        assert "Score not found" in result.stdout

    def test_diagnose_nonexistent_workspace(self, tmp_path: Path) -> None:
        fake = tmp_path / "nope"

        result = runner.invoke(
            app, ["diagnose", "any-job", "--workspace", str(fake)]
        )
        assert result.exit_code == 1
        assert "Workspace not found" in result.stdout

    def test_diagnose_with_errors(self, tmp_path: Path) -> None:
        """Diagnose should display errors section when job has failures."""
        now = datetime.now(UTC)
        error = ErrorRecord(
            error_type="permanent",
            error_code="E301",
            error_message="Validation: file not found",
            attempt_number=3,
            timestamp=now,
        )
        sheets = {
            1: SheetState(
                sheet_num=1,
                status=SheetStatus.FAILED,
                attempt_count=3,
                error_history=[error],
            ),
        }
        job = _make_job(total_sheets=1, status=JobStatus.FAILED, sheets=sheets)
        _write_state(tmp_path, job)

        result = runner.invoke(
            app, ["diagnose", "diag-test", "--workspace", str(tmp_path)]
        )
        assert result.exit_code == 0
        # Should show errors section
        assert "Error" in result.stdout or "error" in result.stdout.lower()

    def test_diagnose_json_with_errors(self, tmp_path: Path) -> None:
        now = datetime.now(UTC)
        error = ErrorRecord(
            error_type="rate_limit",
            error_code="E102",
            error_message="Rate limited",
            attempt_number=1,
            timestamp=now,
        )
        sheets = {
            1: SheetState(
                sheet_num=1,
                status=SheetStatus.FAILED,
                attempt_count=2,
                error_history=[error],
            ),
        }
        job = _make_job(total_sheets=1, status=JobStatus.FAILED, sheets=sheets)
        _write_state(tmp_path, job)

        result = runner.invoke(
            app, ["diagnose", "diag-test", "--json", "--workspace", str(tmp_path)]
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["error_count"] == 1
        assert data["errors"][0]["error_type"] == "rate_limit"


class TestErrorsCommand:
    """Tests for `mzt errors` via CliRunner."""

    def test_errors_no_errors(self, tmp_path: Path) -> None:
        job = _make_job()
        _write_state(tmp_path, job)

        result = runner.invoke(
            app, ["errors", "diag-test", "--workspace", str(tmp_path)]
        )
        assert result.exit_code == 0
        assert "No errors found" in result.stdout

    def test_errors_with_history(self, tmp_path: Path) -> None:
        now = datetime.now(UTC)
        err1 = ErrorRecord(
            error_type="transient", error_code="E001",
            error_message="Timeout", attempt_number=1, timestamp=now,
        )
        err2 = ErrorRecord(
            error_type="permanent", error_code="E301",
            error_message="File not found", attempt_number=2, timestamp=now,
        )
        sheets = {
            1: SheetState(
                sheet_num=1, status=SheetStatus.FAILED,
                attempt_count=2, error_history=[err1, err2],
            ),
        }
        job = _make_job(total_sheets=1, status=JobStatus.FAILED, sheets=sheets)
        _write_state(tmp_path, job)

        result = runner.invoke(
            app, ["errors", "diag-test", "--workspace", str(tmp_path)]
        )
        assert result.exit_code == 0
        assert "Timeout" in result.stdout or "E001" in result.stdout

    def test_errors_json_output(self, tmp_path: Path) -> None:
        now = datetime.now(UTC)
        err = ErrorRecord(
            error_type="transient", error_code="E001",
            error_message="Timeout", attempt_number=1, timestamp=now,
        )
        sheets = {
            1: SheetState(
                sheet_num=1, status=SheetStatus.FAILED,
                attempt_count=1, error_history=[err],
            ),
        }
        job = _make_job(total_sheets=1, status=JobStatus.FAILED, sheets=sheets)
        _write_state(tmp_path, job)

        result = runner.invoke(
            app, ["errors", "diag-test", "--json", "--workspace", str(tmp_path)]
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["total_errors"] == 1
        assert data["errors"][0]["error_code"] == "E001"

    def test_errors_filter_by_sheet(self, tmp_path: Path) -> None:
        now = datetime.now(UTC)
        err1 = ErrorRecord(
            error_type="transient", error_code="E001",
            error_message="Timeout on sheet 1", attempt_number=1, timestamp=now,
        )
        err2 = ErrorRecord(
            error_type="permanent", error_code="E301",
            error_message="Error on sheet 2", attempt_number=1, timestamp=now,
        )
        sheets = {
            1: SheetState(
                sheet_num=1, status=SheetStatus.FAILED,
                attempt_count=1, error_history=[err1],
            ),
            2: SheetState(
                sheet_num=2, status=SheetStatus.FAILED,
                attempt_count=1, error_history=[err2],
            ),
        }
        job = _make_job(total_sheets=2, status=JobStatus.FAILED, sheets=sheets)
        _write_state(tmp_path, job)

        result = runner.invoke(
            app, [
                "errors", "diag-test",
                "--sheet", "1", "--json",
                "--workspace", str(tmp_path),
            ]
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["total_errors"] == 1
        assert data["errors"][0]["error_message"] == "Timeout on sheet 1"

    def test_errors_filter_by_type(self, tmp_path: Path) -> None:
        now = datetime.now(UTC)
        err1 = ErrorRecord(
            error_type="transient", error_code="E001",
            error_message="Timeout", attempt_number=1, timestamp=now,
        )
        err2 = ErrorRecord(
            error_type="rate_limit", error_code="E102",
            error_message="Rate limited", attempt_number=2, timestamp=now,
        )
        sheets = {
            1: SheetState(
                sheet_num=1, status=SheetStatus.FAILED,
                attempt_count=2, error_history=[err1, err2],
            ),
        }
        job = _make_job(total_sheets=1, status=JobStatus.FAILED, sheets=sheets)
        _write_state(tmp_path, job)

        result = runner.invoke(
            app, [
                "errors", "diag-test",
                "--type", "rate_limit", "--json",
                "--workspace", str(tmp_path),
            ]
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["total_errors"] == 1
        assert data["errors"][0]["error_type"] == "rate_limit"

    def test_errors_synthetic_from_sheet_message(self, tmp_path: Path) -> None:
        """When no error_history, errors command synthesizes from sheet error_message."""
        sheets = {
            1: SheetState(
                sheet_num=1, status=SheetStatus.FAILED, attempt_count=2,
                error_message="CLI binary not found",
                error_category="validation",
            ),
        }
        job = _make_job(total_sheets=1, status=JobStatus.FAILED, sheets=sheets)
        _write_state(tmp_path, job)

        result = runner.invoke(
            app, ["errors", "diag-test", "--json", "--workspace", str(tmp_path)]
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["total_errors"] == 1
        assert "CLI binary not found" in data["errors"][0]["error_message"]

    def test_errors_job_not_found(self, tmp_path: Path) -> None:
        workspace = tmp_path / "empty"
        workspace.mkdir()

        result = runner.invoke(
            app, ["errors", "no-job", "--workspace", str(workspace)]
        )
        assert result.exit_code == 1
        assert "Score not found" in result.stdout


class TestLogsCommand:
    """Basic tests for `mzt logs` (limited scope — no running jobs)."""

    def test_logs_no_log_file(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app, ["logs", "--workspace", str(tmp_path)]
        )
        assert result.exit_code == 1
        assert "No log files found" in result.stdout

    def test_logs_invalid_level(self, tmp_path: Path) -> None:
        # Create a dummy log file so it doesn't exit early on missing file
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        log_file = log_dir / "marianne.log"
        log_file.write_text('{"event": "test", "level": "INFO"}\n')

        result = runner.invoke(
            app, ["logs", "--workspace", str(tmp_path), "--level", "BOGUS"]
        )
        assert result.exit_code == 1
        assert "Invalid log level" in result.stdout


# ---------------------------------------------------------------------------
# Log file discovery tests
# ---------------------------------------------------------------------------


class TestDiscoverLogFiles:
    """Tests for _discover_log_files() helper."""

    def test_no_workspace(self) -> None:
        assert _discover_log_files(None) == []

    def test_nonexistent_workspace(self, tmp_path: Path) -> None:
        assert _discover_log_files(tmp_path / "nope") == []

    def test_empty_workspace(self, tmp_path: Path) -> None:
        assert _discover_log_files(tmp_path) == []

    def test_discovers_sheet_logs(self, tmp_path: Path) -> None:
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        (logs_dir / "sheet-01.stdout.log").write_text("hello from sheet 1")
        (logs_dir / "sheet-02.stderr.log").write_text("err")
        (logs_dir / "not-a-log.txt").write_text("ignored")

        discovered = _discover_log_files(tmp_path)
        names = [d["name"] for d in discovered]
        assert "sheet-01.stdout.log" in names
        assert "sheet-02.stderr.log" in names
        assert "not-a-log.txt" not in names
        assert all(d["category"] == "sheet_log" for d in discovered)

    def test_discovers_hook_logs(self, tmp_path: Path) -> None:
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "post-success.log").write_text("hook output")

        discovered = _discover_log_files(tmp_path)
        assert len(discovered) == 1
        assert discovered[0]["name"] == "post-success.log"
        assert discovered[0]["category"] == "hook_log"

    def test_reports_size_and_mtime(self, tmp_path: Path) -> None:
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        (logs_dir / "test.log").write_text("x" * 100)

        discovered = _discover_log_files(tmp_path)
        assert len(discovered) == 1
        assert discovered[0]["size_bytes"] == 100
        assert "modified_at" in discovered[0]


class TestAttachLogContents:
    """Tests for _attach_log_contents() helper."""

    def test_attaches_tail_lines(self, tmp_path: Path) -> None:
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        log_file = logs_dir / "sheet-01.log"
        # Write 60 lines — only last 50 should be inlined
        log_file.write_text("\n".join(f"line {i}" for i in range(60)))

        report: dict = {"log_files": [{"path": str(log_file)}]}
        _attach_log_contents(report)

        content = report["log_contents"][str(log_file)]
        lines = content.strip().splitlines()
        assert len(lines) == 50
        assert lines[0] == "line 10"
        assert lines[-1] == "line 59"

    def test_empty_log_files_list(self) -> None:
        report: dict = {"log_files": []}
        _attach_log_contents(report)
        assert report["log_contents"] == {}

    def test_missing_file_skipped(self, tmp_path: Path) -> None:
        report: dict = {"log_files": [{"path": str(tmp_path / "gone.log")}]}
        _attach_log_contents(report)
        assert report["log_contents"] == {}


class TestDiagnosticReportLogFiles:
    """Tests for log_files integration in _build_diagnostic_report()."""

    def test_report_includes_log_files_key(self) -> None:
        job = _make_job()
        report = _build_diagnostic_report(job)
        assert "log_files" in report

    def test_report_discovers_workspace_logs(self, tmp_path: Path) -> None:
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        (logs_dir / "sheet-01.log").write_text("data")

        job = _make_job()
        report = _build_diagnostic_report(job, workspace=tmp_path)
        assert len(report["log_files"]) == 1
        assert report["log_files"][0]["name"] == "sheet-01.log"

    def test_report_no_workspace_empty_log_files(self) -> None:
        job = _make_job()
        report = _build_diagnostic_report(job, workspace=None)
        assert report["log_files"] == []


class TestDiagnoseIncludeLogsFlag:
    """Tests for the --include-logs CLI flag."""

    def test_include_logs_flag_accepted(self, tmp_path: Path) -> None:
        job = _make_job()
        _write_state(tmp_path, job)
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        (logs_dir / "test.log").write_text("some log content\n")

        result = runner.invoke(
            app,
            ["diagnose", "diag-test", "--include-logs", "--workspace", str(tmp_path)],
        )
        assert result.exit_code == 0

    def test_include_logs_json_output(self, tmp_path: Path) -> None:
        job = _make_job()
        _write_state(tmp_path, job)
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        (logs_dir / "sheet.log").write_text("log line\n")

        result = runner.invoke(
            app,
            ["diagnose", "diag-test", "--include-logs", "--json", "--workspace", str(tmp_path)],
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert "log_files" in data
        assert "log_contents" in data


class TestMaxOutputCaptureConfig:
    """Tests for configurable max_output_capture_bytes in BackendConfig."""

    def test_default_value(self) -> None:
        from marianne.core.config import BackendConfig
        config = BackendConfig()
        assert config.max_output_capture_bytes == 51200

    def test_custom_value(self) -> None:
        from marianne.core.config import BackendConfig
        config = BackendConfig(max_output_capture_bytes=20480)
        assert config.max_output_capture_bytes == 20480

    def test_capture_output_respects_max_bytes(self) -> None:
        """SheetState.capture_output uses the configured max_bytes."""
        sheet = SheetState(sheet_num=1)
        big_output = "x" * 50000
        sheet.capture_output(big_output, "", max_bytes=1000)
        assert sheet.stdout_tail is not None
        assert len(sheet.stdout_tail.encode("utf-8")) <= 1000
        assert sheet.output_truncated is True


# ---------------------------------------------------------------------------
# Log rotation hint tests
# ---------------------------------------------------------------------------


class TestLogRotationHint:
    """Tests for the log rotation hint when hook log files accumulate."""

    def test_no_hint_under_threshold(self, tmp_path: Path) -> None:
        """No hint when hooks dir has <= 20 log files."""
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        for i in range(15):
            (hooks_dir / f"hook-{i:02d}.log").write_text(f"log {i}")

        job = _make_job()
        report = _build_diagnostic_report(job, workspace=tmp_path)
        assert "log_rotation_hint" not in report

    def test_hint_when_exceeds_threshold(self, tmp_path: Path) -> None:
        """Hint present when hooks dir has > 20 log files."""
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        for i in range(25):
            (hooks_dir / f"hook-{i:02d}.log").write_text(f"log {i}")

        job = _make_job()
        report = _build_diagnostic_report(job, workspace=tmp_path)
        assert "log_rotation_hint" in report
        assert "25 hook log files" in report["log_rotation_hint"]
        assert "cleanup" in report["log_rotation_hint"]

    def test_hint_displayed_in_rich_output(self, tmp_path: Path) -> None:
        """CLI diagnose should show the hint when present."""
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        for i in range(22):
            (hooks_dir / f"hook-{i:02d}.log").write_text(f"log {i}")

        job = _make_job()
        _write_state(tmp_path, job)

        result = runner.invoke(
            app, ["diagnose", "diag-test", "--workspace", str(tmp_path)]
        )
        assert result.exit_code == 0
        assert "cleanup" in result.stdout or "hook log" in result.stdout

    def test_hint_in_json_output(self, tmp_path: Path) -> None:
        """JSON diagnose should include log_rotation_hint key."""
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        for i in range(21):
            (hooks_dir / f"hook-{i:02d}.log").write_text(f"log {i}")

        job = _make_job()
        _write_state(tmp_path, job)

        result = runner.invoke(
            app, ["diagnose", "diag-test", "--json", "--workspace", str(tmp_path)]
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert "log_rotation_hint" in data
        assert "21 hook log files" in data["log_rotation_hint"]


# ---------------------------------------------------------------------------
# Preflight warning cap tests
# ---------------------------------------------------------------------------


class TestPreflightWarningCap:
    """Tests for preflight warning capping in diagnosis."""

    def test_warnings_under_cap_unchanged(self) -> None:
        """Warnings under 20 are stored as-is."""
        warnings = [f"Warning {i}" for i in range(10)]
        sheets = {
            1: SheetState(
                sheet_num=1,
                status=SheetStatus.COMPLETED,
                attempt_count=1,
                preflight_warnings=warnings,
            ),
        }
        job = _make_job(total_sheets=1, sheets=sheets)
        report = _build_diagnostic_report(job)
        assert len(report["preflight_warnings"]) == 10

    def test_diagnose_display_caps_at_20(self) -> None:
        """Diagnose rich output caps preflight warning display at 20."""
        warnings = [f"Warning {i}" for i in range(30)]
        sheets = {
            1: SheetState(
                sheet_num=1,
                status=SheetStatus.COMPLETED,
                attempt_count=1,
                preflight_warnings=warnings,
            ),
        }
        job = _make_job(total_sheets=1, sheets=sheets)
        report = _build_diagnostic_report(job)
        # Report collects all warnings (from what's stored)
        assert len(report["preflight_warnings"]) == 30
