"""Tests for status command helper functions and CLI integration.

Covers internal helper functions (_collect_recent_errors, _infer_circuit_breaker_state,
get_last_activity_time, _infer_error_type, _format_daemon_timestamp), list_jobs
command, JSON output schema, cost/hook/synthesis rendering, and additional CLI edge cases.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from marianne.cli import app
from marianne.cli.commands.status import (
    _collect_recent_errors,
    _format_daemon_timestamp,
    _infer_circuit_breaker_state,
    _infer_error_type,
    _render_cost_summary,
)
from marianne.cli.helpers import get_last_activity_time
from marianne.core.checkpoint import (
    CheckpointState,
    ErrorRecord,
    JobStatus,
    SheetState,
    SheetStatus,
)

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent status helper tests from reaching a real conductor."""
    async def _fake_route(
        method: str, params: dict, *, socket_path=None
    ) -> tuple[bool, None]:
        return False, None

    monkeypatch.setattr(
        "marianne.daemon.detect.try_daemon_route", _fake_route,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_job(
    *,
    job_id: str = "status-test",
    total_sheets: int = 5,
    status: JobStatus = JobStatus.COMPLETED,
    sheets: dict[int, SheetState] | None = None,
) -> CheckpointState:
    """Build a minimal CheckpointState for status tests."""
    now = datetime.now(UTC)
    if sheets is None:
        sheets = {
            i: SheetState(
                sheet_num=i,
                status=SheetStatus.COMPLETED,
                attempt_count=1,
            )
            for i in range(1, total_sheets + 1)
        }
    return CheckpointState(
        job_id=job_id,
        job_name="Status Test Job",
        total_sheets=total_sheets,
        last_completed_sheet=max(sheets.keys()) if sheets else 0,
        status=status,
        created_at=now - timedelta(hours=1),
        updated_at=now,
        sheets=sheets,
    )


def _write_state(tmp_path: Path, state: CheckpointState) -> Path:
    fp = tmp_path / f"{state.job_id}.json"
    fp.write_text(json.dumps(state.model_dump(mode="json"), default=str))
    return tmp_path


# ---------------------------------------------------------------------------
# _infer_error_type tests
# ---------------------------------------------------------------------------


class TestInferErrorType:
    """Tests for the _infer_error_type() helper."""

    def test_none_category_returns_permanent(self) -> None:
        assert _infer_error_type(None) == "permanent"

    def test_rate_limit_category(self) -> None:
        assert _infer_error_type("rate_limit") == "rate_limit"
        assert _infer_error_type("RATE_LIMIT") == "rate_limit"
        assert _infer_error_type("Rate Limit Exceeded") == "rate_limit"

    def test_transient_categories(self) -> None:
        for category in ("transient", "timeout", "network", "signal"):
            assert _infer_error_type(category) == "transient"

    def test_unknown_category_returns_permanent(self) -> None:
        assert _infer_error_type("validation") == "permanent"
        assert _infer_error_type("E301") == "permanent"


# ---------------------------------------------------------------------------
# _collect_recent_errors tests
# ---------------------------------------------------------------------------


class TestCollectRecentErrors:
    """Tests for the _collect_recent_errors() helper."""

    def test_no_errors_returns_empty(self) -> None:
        job = _make_job()
        assert _collect_recent_errors(job) == []

    def test_collects_from_error_history(self) -> None:
        now = datetime.now(UTC)
        err = ErrorRecord(
            error_type="transient",
            error_code="E001",
            error_message="Timeout",
            attempt_number=1,
            timestamp=now,
        )
        sheets = {
            1: SheetState(
                sheet_num=1,
                status=SheetStatus.FAILED,
                attempt_count=1,
                error_history=[err],
            ),
        }
        job = _make_job(sheets=sheets, total_sheets=1, status=JobStatus.FAILED)
        result = _collect_recent_errors(job, limit=10)

        assert len(result) == 1
        assert result[0][0] == 1  # sheet_num
        assert result[0][1].error_code == "E001"

    def test_limit_respected(self) -> None:
        now = datetime.now(UTC)
        errors = [
            ErrorRecord(
                error_type="transient",
                error_code=f"E00{i}",
                error_message=f"Error {i}",
                attempt_number=i,
                timestamp=now - timedelta(minutes=5 - i),
            )
            for i in range(1, 6)
        ]
        sheets = {
            1: SheetState(
                sheet_num=1,
                status=SheetStatus.FAILED,
                attempt_count=5,
                error_history=errors,
            ),
        }
        job = _make_job(sheets=sheets, total_sheets=1, status=JobStatus.FAILED)
        result = _collect_recent_errors(job, limit=2)

        assert len(result) == 2

    def test_sorted_by_timestamp_descending(self) -> None:
        now = datetime.now(UTC)
        old_err = ErrorRecord(
            error_type="transient", error_code="E001",
            error_message="Old", attempt_number=1,
            timestamp=now - timedelta(hours=1),
        )
        new_err = ErrorRecord(
            error_type="transient", error_code="E002",
            error_message="New", attempt_number=2,
            timestamp=now,
        )
        sheets = {
            1: SheetState(
                sheet_num=1, status=SheetStatus.FAILED,
                attempt_count=2, error_history=[old_err, new_err],
            ),
        }
        job = _make_job(sheets=sheets, total_sheets=1, status=JobStatus.FAILED)
        result = _collect_recent_errors(job, limit=10)

        # Most recent first
        assert result[0][1].error_code == "E002"
        assert result[1][1].error_code == "E001"

    def test_synthetic_from_sheet_error_message(self) -> None:
        """When no error_history exists, synthetic ErrorRecord is created."""
        now = datetime.now(UTC)
        sheets = {
            1: SheetState(
                sheet_num=1,
                status=SheetStatus.FAILED,
                attempt_count=3,
                error_message="CLI not found",
                error_category="validation",
                completed_at=now,
            ),
        }
        job = _make_job(sheets=sheets, total_sheets=1, status=JobStatus.FAILED)
        result = _collect_recent_errors(job, limit=10)

        assert len(result) == 1
        assert result[0][1].error_message == "CLI not found"
        assert result[0][1].error_code == "E201"  # "validation" → E201 via format_error_code_for_display


# ---------------------------------------------------------------------------
# get_last_activity_time tests
# ---------------------------------------------------------------------------


class TestGetLastActivityTime:
    """Tests for the get_last_activity_time() helper."""

    def test_returns_updated_at_for_empty_job(self) -> None:
        """Even a minimal job has updated_at, so it should return that."""
        job = _make_job(total_sheets=1, sheets={})
        job.last_completed_sheet = 0
        # updated_at is always set (non-optional), so result should be non-None
        result = get_last_activity_time(job)
        assert result is not None
        assert result == job.updated_at

    def test_returns_updated_at(self) -> None:
        now = datetime.now(UTC)
        job = _make_job()
        job.updated_at = now
        result = get_last_activity_time(job)
        assert result == now

    def test_prefers_most_recent_activity(self) -> None:
        now = datetime.now(UTC)
        sheet_activity = now + timedelta(minutes=5)
        sheets = {
            1: SheetState(
                sheet_num=1,
                status=SheetStatus.COMPLETED,
                attempt_count=1,
                last_activity_at=sheet_activity,
            ),
        }
        job = _make_job(sheets=sheets, total_sheets=1)
        job.updated_at = now
        result = get_last_activity_time(job)
        assert result == sheet_activity


# ---------------------------------------------------------------------------
# _infer_circuit_breaker_state tests
# ---------------------------------------------------------------------------


class TestInferCircuitBreakerState:
    """Tests for the _infer_circuit_breaker_state() helper."""

    def test_no_sheets_returns_none(self) -> None:
        job = _make_job(total_sheets=1, sheets={})
        job.last_completed_sheet = 0
        assert _infer_circuit_breaker_state(job) is None

    def test_all_completed_returns_none(self) -> None:
        """No failures means no circuit breaker info to show."""
        job = _make_job(total_sheets=3)
        assert _infer_circuit_breaker_state(job) is None

    def test_single_failure_returns_closed(self) -> None:
        sheets = {
            1: SheetState(sheet_num=1, status=SheetStatus.COMPLETED, attempt_count=1),
            2: SheetState(sheet_num=2, status=SheetStatus.FAILED, attempt_count=3),
        }
        job = _make_job(sheets=sheets, total_sheets=2, status=JobStatus.FAILED)
        result = _infer_circuit_breaker_state(job)

        assert result is not None
        assert result["state"] == "closed"
        assert result["consecutive_failures"] == 1

    def test_many_failures_returns_open(self) -> None:
        """5+ consecutive failures from the end should be OPEN."""
        sheets = {
            i: SheetState(
                sheet_num=i,
                status=SheetStatus.FAILED,
                attempt_count=3,
            )
            for i in range(1, 7)
        }
        job = _make_job(sheets=sheets, total_sheets=6, status=JobStatus.FAILED)
        result = _infer_circuit_breaker_state(job)

        assert result is not None
        assert result["state"] == "open"
        assert result["consecutive_failures"] >= 5

    def test_mixed_then_failures(self) -> None:
        """Completed sheets followed by failures — count only consecutive from end."""
        sheets = {
            1: SheetState(sheet_num=1, status=SheetStatus.COMPLETED, attempt_count=1),
            2: SheetState(sheet_num=2, status=SheetStatus.COMPLETED, attempt_count=1),
            3: SheetState(sheet_num=3, status=SheetStatus.FAILED, attempt_count=3),
            4: SheetState(sheet_num=4, status=SheetStatus.FAILED, attempt_count=3),
        }
        job = _make_job(sheets=sheets, total_sheets=4, status=JobStatus.FAILED)
        result = _infer_circuit_breaker_state(job)

        assert result is not None
        assert result["state"] == "closed"
        assert result["consecutive_failures"] == 2

    def test_pending_sheets_ignored(self) -> None:
        """PENDING sheets don't count as failures or break the streak."""
        sheets = {
            1: SheetState(sheet_num=1, status=SheetStatus.COMPLETED, attempt_count=1),
            2: SheetState(sheet_num=2, status=SheetStatus.PENDING, attempt_count=0),
            3: SheetState(sheet_num=3, status=SheetStatus.FAILED, attempt_count=3),
        }
        job = _make_job(sheets=sheets, total_sheets=3, status=JobStatus.FAILED)
        result = _infer_circuit_breaker_state(job)

        assert result is not None
        # Sheet 3 failed, sheet 2 pending (skipped), sheet 1 completed (breaks)
        assert result["consecutive_failures"] >= 1


# ---------------------------------------------------------------------------
# Additional CLI integration tests
# ---------------------------------------------------------------------------


class TestStatusCommandEdgeCases:
    """Additional status CLI tests beyond those in test_cli.py."""

    def test_status_with_parallel_info(self, tmp_path: Path) -> None:
        """Test status displays parallel execution info."""
        state = CheckpointState(
            job_id="parallel-job",
            job_name="Parallel Test",
            total_sheets=10,
            last_completed_sheet=3,
            status=JobStatus.RUNNING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            parallel_enabled=True,
            parallel_max_concurrent=3,
            parallel_batches_executed=1,
            sheets_in_progress=[4, 5, 6],
            sheets={
                i: SheetState(sheet_num=i, status=SheetStatus.COMPLETED, attempt_count=1)
                for i in range(1, 4)
            },
        )
        _write_state(tmp_path, state)

        result = runner.invoke(
            app, ["status", "parallel-job", "--workspace", str(tmp_path)]
        )
        assert result.exit_code == 0
        assert "Parallel" in result.stdout

    def test_status_with_circuit_breaker_inference(self, tmp_path: Path) -> None:
        """Status should show circuit breaker state when many sheets fail."""
        sheets = {
            i: SheetState(sheet_num=i, status=SheetStatus.FAILED, attempt_count=3)
            for i in range(1, 7)
        }
        state = CheckpointState(
            job_id="cb-job",
            job_name="CB Test",
            total_sheets=6,
            last_completed_sheet=0,
            status=JobStatus.FAILED,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            sheets=sheets,
        )
        _write_state(tmp_path, state)

        result = runner.invoke(
            app, ["status", "cb-job", "--workspace", str(tmp_path)]
        )
        assert result.exit_code == 0
        assert "Circuit Breaker" in result.stdout
        assert "OPEN" in result.stdout

    def test_status_json_circuit_breaker(self, tmp_path: Path) -> None:
        """JSON output should include circuit_breaker field."""
        sheets = {
            i: SheetState(sheet_num=i, status=SheetStatus.FAILED, attempt_count=3)
            for i in range(1, 7)
        }
        state = CheckpointState(
            job_id="cb-json-job",
            job_name="CB JSON Test",
            total_sheets=6,
            last_completed_sheet=0,
            status=JobStatus.FAILED,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            sheets=sheets,
        )
        _write_state(tmp_path, state)

        result = runner.invoke(
            app, ["status", "cb-json-job", "--json", "--workspace", str(tmp_path)]
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["circuit_breaker"] is not None
        assert data["circuit_breaker"]["state"] == "open"

    def test_status_with_recent_errors(self, tmp_path: Path) -> None:
        """Status should show recent errors section."""
        now = datetime.now(UTC)
        err = ErrorRecord(
            error_type="permanent", error_code="E301",
            error_message="Validation failed", attempt_number=3,
            timestamp=now,
        )
        sheets = {
            1: SheetState(
                sheet_num=1, status=SheetStatus.FAILED,
                attempt_count=3, error_history=[err],
            ),
        }
        state = CheckpointState(
            job_id="err-job",
            job_name="Error Test",
            total_sheets=1,
            last_completed_sheet=0,
            status=JobStatus.FAILED,
            created_at=now,
            updated_at=now,
            sheets=sheets,
        )
        _write_state(tmp_path, state)

        result = runner.invoke(
            app, ["status", "err-job", "--workspace", str(tmp_path)]
        )
        assert result.exit_code == 0
        assert "Error" in result.stdout or "error" in result.stdout.lower()


# ---------------------------------------------------------------------------
# _format_daemon_timestamp tests
# ---------------------------------------------------------------------------


class TestFormatDaemonTimestamp:
    """Tests for the _format_daemon_timestamp() helper."""

    def test_none_returns_dash(self) -> None:
        assert _format_daemon_timestamp(None) == "-"

    def test_valid_timestamp(self) -> None:
        # 2026-01-15 12:00:00 UTC
        ts = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC).timestamp()
        result = _format_daemon_timestamp(ts)
        assert "2026-01-15" in result
        assert "12:00" in result

    def test_epoch_zero(self) -> None:
        result = _format_daemon_timestamp(0.0)
        assert "1970" in result


# ---------------------------------------------------------------------------
# JSON output schema tests
# ---------------------------------------------------------------------------


class TestStatusJsonOutput:
    """Tests for _output_status_json() via CLI invocation."""

    def test_json_output_has_required_keys(self, tmp_path: Path) -> None:
        """JSON output should include all documented top-level keys."""
        state = CheckpointState(
            job_id="json-schema-test",
            job_name="JSON Schema Test",
            total_sheets=2,
            last_completed_sheet=1,
            status=JobStatus.RUNNING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            sheets={
                1: SheetState(sheet_num=1, status=SheetStatus.COMPLETED, attempt_count=1),
                2: SheetState(sheet_num=2, status=SheetStatus.PENDING, attempt_count=0),
            },
        )
        _write_state(tmp_path, state)

        result = runner.invoke(
            app, ["status", "json-schema-test", "--json", "--workspace", str(tmp_path)]
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)

        # Verify all required top-level keys
        required_keys = {
            "job_id", "job_name", "status", "progress", "timing",
            "execution", "cost", "circuit_breaker", "hook_results_count",
            "hook_failures", "recent_errors", "error", "sheets",
        }
        assert required_keys.issubset(data.keys())

    def test_json_progress_structure(self, tmp_path: Path) -> None:
        """JSON progress block should have completed/total/percent."""
        state = CheckpointState(
            job_id="progress-test",
            job_name="Progress Test",
            total_sheets=10,
            last_completed_sheet=7,
            status=JobStatus.RUNNING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            sheets={
                i: SheetState(sheet_num=i, status=SheetStatus.COMPLETED, attempt_count=1)
                for i in range(1, 8)
            },
        )
        _write_state(tmp_path, state)

        result = runner.invoke(
            app, ["status", "progress-test", "--json", "--workspace", str(tmp_path)]
        )
        data = json.loads(result.stdout)
        assert data["progress"]["completed"] == 7
        assert data["progress"]["total"] == 10
        assert data["progress"]["percent"] == 70.0

    def test_json_cost_data(self, tmp_path: Path) -> None:
        """JSON output should include cost tracking fields."""
        state = CheckpointState(
            job_id="cost-test",
            job_name="Cost Test",
            total_sheets=1,
            last_completed_sheet=1,
            status=JobStatus.COMPLETED,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            total_estimated_cost=1.23,
            total_input_tokens=5000,
            total_output_tokens=3000,
            sheets={
                1: SheetState(
                    sheet_num=1, status=SheetStatus.COMPLETED,
                    attempt_count=1, estimated_cost=1.23,
                ),
            },
        )
        _write_state(tmp_path, state)

        result = runner.invoke(
            app, ["status", "cost-test", "--json", "--workspace", str(tmp_path)]
        )
        data = json.loads(result.stdout)
        assert data["cost"]["total_estimated_cost"] == 1.23
        assert data["cost"]["total_input_tokens"] == 5000
        assert data["cost"]["total_output_tokens"] == 3000

    def test_json_recent_errors_structure(self, tmp_path: Path) -> None:
        """JSON recent_errors should contain structured error records."""
        now = datetime.now(UTC)
        err = ErrorRecord(
            error_type="transient", error_code="E001",
            error_message="Timeout", attempt_number=2,
            timestamp=now,
        )
        state = CheckpointState(
            job_id="json-errors-test",
            job_name="JSON Errors Test",
            total_sheets=1,
            last_completed_sheet=0,
            status=JobStatus.FAILED,
            created_at=now,
            updated_at=now,
            sheets={
                1: SheetState(
                    sheet_num=1, status=SheetStatus.FAILED,
                    attempt_count=2, error_history=[err],
                ),
            },
        )
        _write_state(tmp_path, state)

        result = runner.invoke(
            app, ["status", "json-errors-test", "--json", "--workspace", str(tmp_path)]
        )
        data = json.loads(result.stdout)
        assert len(data["recent_errors"]) >= 1
        error_item = data["recent_errors"][0]
        assert "sheet_num" in error_item
        assert "error_type" in error_item
        assert "error_code" in error_item
        assert "error_message" in error_item

    def test_json_sheets_with_duration(self, tmp_path: Path) -> None:
        """JSON sheets should include execution_duration_seconds when present."""
        state = CheckpointState(
            job_id="duration-test",
            job_name="Duration Test",
            total_sheets=1,
            last_completed_sheet=1,
            status=JobStatus.COMPLETED,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            sheets={
                1: SheetState(
                    sheet_num=1, status=SheetStatus.COMPLETED,
                    attempt_count=1, execution_duration_seconds=42.5,
                ),
            },
        )
        _write_state(tmp_path, state)

        result = runner.invoke(
            app, ["status", "duration-test", "--json", "--workspace", str(tmp_path)]
        )
        data = json.loads(result.stdout)
        assert data["sheets"]["1"]["execution_duration_seconds"] == 42.5


# ---------------------------------------------------------------------------
# list_jobs CLI tests
# ---------------------------------------------------------------------------


class TestListJobsCommand:
    """Tests for the 'mozart list' command (daemon registry-based)."""

    @staticmethod
    def _mock_daemon_route(jobs: list[dict[str, Any]]):
        """Return a patch that makes try_daemon_route return the given jobs."""
        return patch(
            "marianne.daemon.detect.try_daemon_route",
            new_callable=AsyncMock,
            return_value=(True, jobs),
        )

    def test_list_no_jobs(self) -> None:
        """Empty registry should show 'No active scores' (default active-only filter)."""
        with self._mock_daemon_route([]):
            result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "No active scores" in result.stdout

    def test_list_finds_job_files(self) -> None:
        """Should display jobs from the daemon registry."""
        jobs = [{"job_id": "list-test-job", "status": "running", "workspace": "/tmp"}]
        with self._mock_daemon_route(jobs):
            result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "list-test-job" in result.stdout

    def test_list_status_filter(self) -> None:
        """--status filter should only show matching jobs."""
        jobs = [
            {"job_id": "running-job", "status": "running", "workspace": "/tmp"},
            {"job_id": "completed-job", "status": "completed", "workspace": "/tmp"},
        ]
        with self._mock_daemon_route(jobs):
            result = runner.invoke(app, ["list", "--status", "completed"])
        assert result.exit_code == 0
        assert "completed-job" in result.stdout
        assert "running-job" not in result.stdout


# ---------------------------------------------------------------------------
# Rich output rendering tests
# ---------------------------------------------------------------------------


class TestStatusRichRendering:
    """Tests for rich-formatted status output sections."""

    def test_cost_summary_displayed(self, tmp_path: Path) -> None:
        """Rich output should show cost summary when data present."""
        state = CheckpointState(
            job_id="cost-rich-test",
            job_name="Cost Rich Test",
            total_sheets=1,
            last_completed_sheet=1,
            status=JobStatus.COMPLETED,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            total_estimated_cost=2.50,
            total_input_tokens=10000,
            total_output_tokens=5000,
            sheets={
                1: SheetState(sheet_num=1, status=SheetStatus.COMPLETED, attempt_count=1),
            },
        )
        _write_state(tmp_path, state)

        result = runner.invoke(
            app, ["status", "cost-rich-test", "--workspace", str(tmp_path)]
        )
        assert result.exit_code == 0
        assert "Cost" in result.stdout
        assert "$2.50" in result.stdout

    def test_hook_results_displayed(self, tmp_path: Path) -> None:
        """Rich output should show hook results when present."""
        state = CheckpointState(
            job_id="hooks-test",
            job_name="Hooks Test",
            total_sheets=1,
            last_completed_sheet=1,
            status=JobStatus.COMPLETED,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            hook_results=[
                {"hook_name": "pre_run", "event": "pre_run", "success": True},
                {"hook_name": "post_run", "event": "post_run", "success": False,
                 "error": "Hook script failed"},
            ],
            sheets={
                1: SheetState(sheet_num=1, status=SheetStatus.COMPLETED, attempt_count=1),
            },
        )
        _write_state(tmp_path, state)

        result = runner.invoke(
            app, ["status", "hooks-test", "--workspace", str(tmp_path)]
        )
        assert result.exit_code == 0
        assert "Hook" in result.stdout

    def test_timing_section_displayed(self, tmp_path: Path) -> None:
        """Rich output should show timing section."""
        now = datetime.now(UTC)
        state = CheckpointState(
            job_id="timing-test",
            job_name="Timing Test",
            total_sheets=1,
            last_completed_sheet=1,
            status=JobStatus.COMPLETED,
            created_at=now - timedelta(hours=1),
            started_at=now - timedelta(minutes=50),
            updated_at=now,
            completed_at=now,
            sheets={
                1: SheetState(sheet_num=1, status=SheetStatus.COMPLETED, attempt_count=1),
            },
        )
        _write_state(tmp_path, state)

        result = runner.invoke(
            app, ["status", "timing-test", "--workspace", str(tmp_path)]
        )
        assert result.exit_code == 0
        assert "Timing" in result.stdout
        assert "Duration" in result.stdout


# ---------------------------------------------------------------------------
# _render_cost_summary tests — always-visible cost tracking (roadmap step 15)
# ---------------------------------------------------------------------------


class TestCostSummaryAlwaysVisible:
    """Verify cost summary is always shown, even with no cost data.

    Before this change, _render_cost_summary was silent when there was
    no cost data. Now it always shows cost info so users are aware of
    API spend, and includes a tip when cost limits are disabled.
    """

    def test_zero_cost_still_shows_summary(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Cost summary is rendered even when total cost is zero."""
        job = _make_job(total_sheets=3)
        _render_cost_summary(job)
        captured = capsys.readouterr()
        assert "Cost Summary" in captured.out
        assert "$0.00" in captured.out

    def test_no_cost_limits_shows_tip(self, capsys: pytest.CaptureFixture[str]) -> None:
        """When cost limits are not configured, a tip is shown."""
        job = _make_job(total_sheets=1)
        _render_cost_summary(job)
        captured = capsys.readouterr()
        assert "cost_limits.enabled" in captured.out

    def test_cost_limits_enabled_no_tip(self, capsys: pytest.CaptureFixture[str]) -> None:
        """When cost limits are enabled, no tip is shown."""
        job = _make_job(total_sheets=1)
        job.config_snapshot = {
            "cost_limits": {"enabled": True, "max_cost_per_job": 10.0}
        }
        _render_cost_summary(job)
        captured = capsys.readouterr()
        assert "Cost Summary" in captured.out
        assert "cost_limits.enabled" not in captured.out
        assert "limit: $10.00" in captured.out

    def test_cost_limit_set_but_not_enabled(self, capsys: pytest.CaptureFixture[str]) -> None:
        """A cost limit exists but enabled=false shows 'not enforced'."""
        job = _make_job(total_sheets=1)
        job.config_snapshot = {
            "cost_limits": {"enabled": False, "max_cost_per_job": 5.0}
        }
        _render_cost_summary(job)
        captured = capsys.readouterr()
        assert "not enforced" in captured.out

    def test_actual_cost_displayed(self, capsys: pytest.CaptureFixture[str]) -> None:
        """When there is actual cost data, it is displayed."""
        job = _make_job(total_sheets=1)
        job.total_estimated_cost = 2.47
        job.total_input_tokens = 15000
        job.total_output_tokens = 8000
        job.config_snapshot = {
            "cost_limits": {"enabled": True, "max_cost_per_job": 10.0}
        }
        _render_cost_summary(job)
        captured = capsys.readouterr()
        assert "$2.47" in captured.out
        assert "15,000" in captured.out
        assert "8,000" in captured.out

    def test_cost_limit_reached_shows_warning(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """When cost limit is reached, a red warning appears."""
        job = _make_job(total_sheets=1)
        job.total_estimated_cost = 10.01
        job.cost_limit_reached = True
        job.config_snapshot = {
            "cost_limits": {"enabled": True, "max_cost_per_job": 10.0}
        }
        _render_cost_summary(job)
        captured = capsys.readouterr()
        assert "limit reached" in captured.out.lower() or "paused" in captured.out.lower()


# ---------------------------------------------------------------------------
# F-071: mozart list --json (Dash, M4)
# ---------------------------------------------------------------------------


class TestListJobsJsonOutput:
    """Tests for --json flag on the list command (F-071)."""

    @staticmethod
    def _mock_daemon_route(jobs: list[dict[str, Any]]):
        """Return a patch that makes try_daemon_route return the given jobs."""
        return patch(
            "marianne.daemon.detect.try_daemon_route",
            new_callable=AsyncMock,
            return_value=(True, jobs),
        )

    def test_list_json_outputs_valid_json(self) -> None:
        """--json flag should produce valid JSON array."""
        jobs = [
            {
                "job_id": "json-test",
                "status": "running",
                "workspace": "/tmp/ws",
                "submitted_at": 1711900000.0,
            }
        ]
        with self._mock_daemon_route(jobs):
            result = runner.invoke(app, ["list", "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.stdout)
        assert isinstance(parsed, list)
        assert len(parsed) == 1
        assert parsed[0]["job_id"] == "json-test"

    def test_list_json_empty_returns_empty_array(self) -> None:
        """--json with no matching jobs returns []."""
        with self._mock_daemon_route([]):
            result = runner.invoke(app, ["list", "--all", "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.stdout)
        assert parsed == []

    def test_list_json_includes_all_fields(self) -> None:
        """JSON output should include id, status, workspace, submitted_at."""
        jobs = [
            {
                "job_id": "field-test",
                "status": "completed",
                "workspace": "/tmp/ws",
                "submitted_at": 1711900000.0,
            }
        ]
        with self._mock_daemon_route(jobs):
            result = runner.invoke(app, ["list", "--all", "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.stdout)
        item = parsed[0]
        assert "job_id" in item
        assert "status" in item
        assert "workspace" in item
        assert "submitted_at" in item

    def test_list_json_respects_status_filter(self) -> None:
        """--json with --status should filter results."""
        jobs = [
            {"job_id": "running-1", "status": "running", "workspace": "/tmp"},
            {"job_id": "done-1", "status": "completed", "workspace": "/tmp"},
        ]
        with self._mock_daemon_route(jobs):
            result = runner.invoke(
                app, ["list", "--status", "completed", "--json"]
            )
        assert result.exit_code == 0
        parsed = json.loads(result.stdout)
        assert len(parsed) == 1
        assert parsed[0]["job_id"] == "done-1"

    def test_list_json_no_conductor_returns_error(self) -> None:
        """--json without conductor should return JSON error."""
        with patch(
            "marianne.daemon.detect.try_daemon_route",
            new_callable=AsyncMock,
            return_value=(False, None),
        ):
            result = runner.invoke(app, ["list", "--json"])
        assert result.exit_code == 1
        # Should contain error info, not a table
        assert "error" in result.stdout.lower() or "conductor" in result.stdout.lower()
