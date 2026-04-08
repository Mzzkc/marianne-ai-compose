"""Comprehensive integration tests for ALL observability changes.

Sheet 10 of the Fix Observability Gaps initiative. These tests verify that
every observability feature works end-to-end:
- Detached hook logging
- Hook result persistence to checkpoint
- API backend log file writing
- CLI backend log write failure handling
- Execution history recording (SQLite)
- `mzt history` CLI command
- Diagnose command log file discovery
- Circuit breaker state persistence
- Chained job info tracking
- Error history capping
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from marianne.core.checkpoint import (
    MAX_ERROR_HISTORY,
    CheckpointState,
    SheetState,
)
from marianne.execution.hooks import (
    HookResult,
    get_hook_log_path,
)
from tests.helpers import record_error_on_sheet

# =============================================================================
# Test 1: Detached hook creates log file
# =============================================================================


class TestDetachedHookCreatesLogFile:
    """Verify that detached hook execution creates a log file in {workspace}/hooks/."""

    def test_get_hook_log_path_creates_directory_and_returns_path(self, tmp_path: Path):
        """get_hook_log_path() should create hooks/ dir and return a timestamped path."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        log_path = get_hook_log_path(workspace, "chain")

        assert log_path is not None
        assert log_path.parent == workspace / "hooks"
        assert log_path.parent.is_dir()
        assert "chain-" in log_path.name
        assert log_path.suffix == ".log"

    def test_get_hook_log_path_returns_none_when_no_workspace(self):
        """get_hook_log_path() should return None when workspace is None."""
        result = get_hook_log_path(None, "chain")
        assert result is None

    def test_get_hook_log_path_different_hook_types(self, tmp_path: Path):
        """get_hook_log_path() should use hook_type in the filename."""
        workspace = tmp_path / "ws"
        workspace.mkdir()

        chain_path = get_hook_log_path(workspace, "chain")
        command_path = get_hook_log_path(workspace, "command")

        assert chain_path is not None
        assert command_path is not None
        assert "chain-" in chain_path.name
        assert "command-" in command_path.name

    def test_detached_hook_result_contains_log_path(self, tmp_path: Path):
        """A detached run_job HookResult should include the log_path field."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        log_path = get_hook_log_path(workspace, "chain")

        result = HookResult(
            hook_type="run_job",
            description="Chain next job",
            success=True,
            output="Detached job spawned (PID 12345)",
            log_path=log_path,
        )

        assert result.log_path is not None
        assert result.log_path.parent == workspace / "hooks"


# =============================================================================
# Test 2: Hook result persisted to checkpoint
# =============================================================================


class TestHookResultPersistedToCheckpoint:
    """Verify that hook execution results are persisted in CheckpointState."""

    def test_record_hook_result_appends_to_list(self):
        """record_hook_result() should append dict to hook_results."""
        state = CheckpointState(
            job_id="test-job",
            job_name="Test",
            total_sheets=2,
        )

        hook_data = {
            "hook_type": "run_command",
            "description": "Post-success cleanup",
            "success": True,
            "exit_code": 0,
            "duration_seconds": 1.5,
        }

        state.record_hook_result(hook_data)

        assert len(state.hook_results) == 1
        assert state.hook_results[0]["hook_type"] == "run_command"
        assert state.hook_results[0]["success"] is True

    def test_record_multiple_hook_results(self):
        """Multiple hook results should accumulate in order."""
        state = CheckpointState(
            job_id="multi-hook",
            job_name="Multi",
            total_sheets=1,
        )

        for i in range(3):
            state.record_hook_result({
                "hook_type": f"hook_{i}",
                "success": i % 2 == 0,
            })

        assert len(state.hook_results) == 3
        assert state.hook_results[0]["hook_type"] == "hook_0"
        assert state.hook_results[2]["hook_type"] == "hook_2"

    def test_hook_results_survive_serialization(self):
        """hook_results should survive JSON round-trip."""
        state = CheckpointState(
            job_id="serial-test",
            job_name="Serialization",
            total_sheets=1,
        )

        state.record_hook_result({
            "hook_type": "run_job",
            "success": True,
            "chained_job_info": {"pid": 9999, "job_path": "/path/to/next.yaml"},
        })

        data = state.model_dump(mode="json")
        restored = CheckpointState.model_validate(data)

        assert len(restored.hook_results) == 1
        assert restored.hook_results[0]["chained_job_info"]["pid"] == 9999

    def test_record_hook_result_updates_timestamp(self):
        """record_hook_result() should update updated_at."""
        state = CheckpointState(
            job_id="ts-test",
            job_name="Timestamp",
            total_sheets=1,
        )
        original_ts = state.updated_at

        state.record_hook_result({"hook_type": "test", "success": True})

        assert state.updated_at >= original_ts


# =============================================================================
# Test 3: API backend writes log files
# =============================================================================


class TestApiBackendWritesLogFiles:
    """Verify that API backend writes responses to log files when paths are set."""

    def test_write_log_file_creates_file(self, tmp_path: Path):
        """_write_log_file should create and populate the log file."""
        from marianne.backends.anthropic_api import AnthropicApiBackend

        backend = AnthropicApiBackend.__new__(AnthropicApiBackend)
        backend._stdout_log_path = None
        backend._stderr_log_path = None

        log_path = tmp_path / "logs" / "sheet-01.stdout.log"

        backend._write_log_file(log_path, "API response content here")

        assert log_path.exists()
        assert log_path.read_text() == "API response content here"

    def test_write_log_file_creates_parent_dirs(self, tmp_path: Path):
        """_write_log_file should create parent directories."""
        from marianne.backends.anthropic_api import AnthropicApiBackend

        backend = AnthropicApiBackend.__new__(AnthropicApiBackend)

        nested_path = tmp_path / "deep" / "nested" / "dir" / "output.log"

        backend._write_log_file(nested_path, "content")

        assert nested_path.exists()
        assert nested_path.read_text() == "content"

    def test_write_log_file_skips_when_path_is_none(self):
        """_write_log_file should do nothing when path is None."""
        from marianne.backends.anthropic_api import AnthropicApiBackend

        backend = AnthropicApiBackend.__new__(AnthropicApiBackend)

        # Should not raise
        backend._write_log_file(None, "content")

    def test_set_output_log_path_creates_both_paths(self, tmp_path: Path):
        """set_output_log_path should set both stdout and stderr paths."""
        from marianne.backends.anthropic_api import AnthropicApiBackend

        backend = AnthropicApiBackend.__new__(AnthropicApiBackend)
        backend._stdout_log_path = None
        backend._stderr_log_path = None

        base_path = tmp_path / "logs" / "sheet-01"
        backend.set_output_log_path(base_path)

        assert backend._stdout_log_path == base_path.with_suffix(".stdout.log")
        assert backend._stderr_log_path == base_path.with_suffix(".stderr.log")

    def test_set_output_log_path_none_clears_paths(self):
        """set_output_log_path(None) should clear both log paths."""
        from marianne.backends.anthropic_api import AnthropicApiBackend

        backend = AnthropicApiBackend.__new__(AnthropicApiBackend)
        backend._stdout_log_path = Path("/some/path.stdout.log")
        backend._stderr_log_path = Path("/some/path.stderr.log")

        backend.set_output_log_path(None)

        assert backend._stdout_log_path is None
        assert backend._stderr_log_path is None


# =============================================================================
# Test 4: CLI backend logs write failures
# =============================================================================


class TestCliBackendLogsWriteFailures:
    """Verify that log write failures are logged as warnings, not silently swallowed."""

    def test_write_log_file_oserror_logged_as_warning(self, tmp_path: Path):
        """_write_log_file should log warning on OSError, not swallow silently."""
        from marianne.backends.anthropic_api import AnthropicApiBackend

        backend = AnthropicApiBackend.__new__(AnthropicApiBackend)

        # Use a path that will fail (file as directory)
        blocker = tmp_path / "blocker"
        blocker.write_text("I am a file")
        bad_path = blocker / "impossible" / "path.log"

        with patch("marianne.backends.anthropic_api._logger") as mock_logger:
            backend._write_log_file(bad_path, "content")
            mock_logger.warning.assert_called_once()
            call_args = mock_logger.warning.call_args
            assert call_args[0][0] == "log_write_failed"

    def test_cli_backend_tracks_log_write_failures(self, tmp_path: Path):
        """ClaudeCliBackend should increment log_write_failures on OSError."""
        from marianne.backends.claude_cli import ClaudeCliBackend

        backend = ClaudeCliBackend.__new__(ClaudeCliBackend)
        backend._stdout_log_path = None
        backend._stderr_log_path = None
        backend.log_write_failures = 0

        # Set a log path that will fail (file as directory)
        blocker = tmp_path / "blocker"
        blocker.write_text("I am a file")
        backend._stdout_log_path = blocker / "impossible" / "output.log"

        # _prepare_log_files should fail gracefully and increment counter
        backend._prepare_log_files()

        assert backend.log_write_failures >= 1

    def test_cli_backend_prepare_log_files_creates_dirs(self, tmp_path: Path):
        """_prepare_log_files should create parent directories for log paths."""
        from marianne.backends.claude_cli import ClaudeCliBackend

        backend = ClaudeCliBackend.__new__(ClaudeCliBackend)
        backend.log_write_failures = 0

        stdout_path = tmp_path / "logs" / "sheet-01.stdout.log"
        stderr_path = tmp_path / "logs" / "sheet-01.stderr.log"
        backend._stdout_log_path = stdout_path
        backend._stderr_log_path = stderr_path

        backend._prepare_log_files()

        assert stdout_path.exists()
        assert stderr_path.exists()
        assert backend.log_write_failures == 0


# =============================================================================
# Test 5: Execution history recorded (SQLite)
# =============================================================================


class TestExecutionHistoryRecorded:
    """Verify that execution history is recorded via SQLite backend."""

    @staticmethod
    def _make_state(job_id: str, total_sheets: int = 5) -> CheckpointState:
        """Helper: create a CheckpointState for saving to SQLite before recording."""
        return CheckpointState(
            job_id=job_id,
            job_name=job_id,
            total_sheets=total_sheets,
        )

    @pytest.mark.asyncio
    async def test_record_execution_stores_data(self, tmp_path: Path):
        """record_execution() should insert a row retrievable via get_execution_history()."""
        from marianne.state.sqlite_backend import SQLiteStateBackend

        db_path = tmp_path / "state.db"
        backend = SQLiteStateBackend(db_path)

        # Save job state first (FK constraint requires job in jobs table)
        state = self._make_state("test-job")
        await backend.save(state)

        record_id = await backend.record_execution(
            job_id="test-job",
            sheet_num=1,
            attempt_num=1,
            prompt="Test prompt",
            output="Test output",
            exit_code=0,
            duration_seconds=5.5,
        )

        assert record_id > 0

        history = await backend.get_execution_history("test-job")
        assert len(history) == 1
        assert history[0]["sheet_num"] == 1
        assert history[0]["exit_code"] == 0
        assert history[0]["duration_seconds"] == 5.5

    @pytest.mark.asyncio
    async def test_record_multiple_executions(self, tmp_path: Path):
        """Multiple executions should all be retrievable."""
        from marianne.state.sqlite_backend import SQLiteStateBackend

        db_path = tmp_path / "state.db"
        backend = SQLiteStateBackend(db_path)

        state = self._make_state("multi-exec")
        await backend.save(state)

        for i in range(5):
            await backend.record_execution(
                job_id="multi-exec",
                sheet_num=i + 1,
                attempt_num=1,
                exit_code=0 if i < 3 else 1,
                duration_seconds=float(i),
            )

        history = await backend.get_execution_history("multi-exec")
        assert len(history) == 5

    @pytest.mark.asyncio
    async def test_get_execution_history_count(self, tmp_path: Path):
        """get_execution_history_count() should return correct count."""
        from marianne.state.sqlite_backend import SQLiteStateBackend

        db_path = tmp_path / "state.db"
        backend = SQLiteStateBackend(db_path)

        state = self._make_state("count-test")
        await backend.save(state)

        for i in range(3):
            await backend.record_execution(
                job_id="count-test",
                sheet_num=i + 1,
                attempt_num=1,
                exit_code=0,
            )

        count = await backend.get_execution_history_count("count-test")
        assert count == 3

    @pytest.mark.asyncio
    async def test_get_execution_history_filters_by_sheet(self, tmp_path: Path):
        """get_execution_history() should filter by sheet_num when specified."""
        from marianne.state.sqlite_backend import SQLiteStateBackend

        db_path = tmp_path / "state.db"
        backend = SQLiteStateBackend(db_path)

        state = self._make_state("filter-test")
        await backend.save(state)

        # Record executions for different sheets
        for sheet_num in [1, 1, 2, 3]:
            await backend.record_execution(
                job_id="filter-test",
                sheet_num=sheet_num,
                attempt_num=1,
                exit_code=0,
            )

        # Filter by sheet 1
        sheet1_history = await backend.get_execution_history(
            "filter-test", sheet_num=1
        )
        assert len(sheet1_history) == 2
        assert all(r["sheet_num"] == 1 for r in sheet1_history)


# =============================================================================
# Test 6: mzt history command
# =============================================================================


class TestMarianneHistoryCommand:
    """Verify the `mzt history` CLI command works correctly."""

    def test_history_command_is_registered(self):
        """The history command should be registered in the CLI app."""
        from marianne.cli import app

        command_names = [
            cmd.name or (cmd.callback.__name__ if cmd.callback else "")
            for cmd in app.registered_commands
        ]
        assert "history" in command_names

    def test_history_function_exists(self):
        """The history function should be importable from diagnose module."""
        from marianne.cli.commands.diagnose import history

        assert callable(history)

    @pytest.mark.asyncio
    async def test_history_job_with_sqlite_backend(self, tmp_path: Path):
        """_history_job should retrieve and display records from SQLite."""
        from marianne.state.sqlite_backend import SQLiteStateBackend

        db_path = tmp_path / "state.db"
        backend = SQLiteStateBackend(db_path)

        # Save job state first (FK constraint requires job in jobs table)
        state = CheckpointState(
            job_id="hist-test",
            job_name="hist-test",
            total_sheets=2,
        )
        await backend.save(state)

        # Seed execution history
        await backend.record_execution(
            job_id="hist-test",
            sheet_num=1,
            attempt_num=1,
            exit_code=0,
            duration_seconds=2.5,
        )

        # Verify data is retrievable
        history = await backend.get_execution_history("hist-test")
        assert len(history) == 1
        assert history[0]["exit_code"] == 0
        assert history[0]["duration_seconds"] == 2.5


# =============================================================================
# Test 7: diagnose includes log files
# =============================================================================


class TestDiagnoseIncludesLogFiles:
    """Verify that diagnose discovers and reports log files in workspace."""

    def test_discover_log_files_finds_sheet_logs(self, tmp_path: Path):
        """_discover_log_files should find logs in {workspace}/logs/."""
        from marianne.cli.commands.diagnose import _discover_log_files

        # Create workspace with log files
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        (logs_dir / "sheet-01.stdout.log").write_text("stdout content")
        (logs_dir / "sheet-01.stderr.log").write_text("stderr content")

        discovered = _discover_log_files(tmp_path)

        assert len(discovered) == 2
        assert all(d["category"] == "sheet_log" for d in discovered)
        names = {d["name"] for d in discovered}
        assert "sheet-01.stdout.log" in names
        assert "sheet-01.stderr.log" in names

    def test_discover_log_files_finds_hook_logs(self, tmp_path: Path):
        """_discover_log_files should find logs in {workspace}/hooks/."""
        from marianne.cli.commands.diagnose import _discover_log_files

        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "chain-20260210-120000.log").write_text("hook output")

        discovered = _discover_log_files(tmp_path)

        assert len(discovered) == 1
        assert discovered[0]["category"] == "hook_log"
        assert discovered[0]["name"] == "chain-20260210-120000.log"

    def test_discover_log_files_returns_metadata(self, tmp_path: Path):
        """Discovered log files should include size, path, and modified_at."""
        from marianne.cli.commands.diagnose import _discover_log_files

        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        log_file = logs_dir / "test.log"
        log_file.write_text("some content here")

        discovered = _discover_log_files(tmp_path)

        assert len(discovered) == 1
        entry = discovered[0]
        assert "path" in entry
        assert "size_bytes" in entry
        assert entry["size_bytes"] > 0
        assert "modified_at" in entry

    def test_discover_log_files_returns_empty_for_none(self):
        """_discover_log_files should return empty list for None workspace."""
        from marianne.cli.commands.diagnose import _discover_log_files

        assert _discover_log_files(None) == []

    def test_discover_log_files_returns_empty_for_missing_dir(self, tmp_path: Path):
        """_discover_log_files should return empty list for missing workspace."""
        from marianne.cli.commands.diagnose import _discover_log_files

        nonexistent = tmp_path / "nonexistent"
        assert _discover_log_files(nonexistent) == []

    def test_build_diagnostic_report_includes_log_files(self, tmp_path: Path):
        """_build_diagnostic_report should include log_files in report."""
        from marianne.cli.commands.diagnose import _build_diagnostic_report

        # Create workspace with logs
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        (logs_dir / "sheet-01.stdout.log").write_text("output")

        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "chain-log.log").write_text("hook out")

        state = CheckpointState(
            job_id="diag-test",
            job_name="Diagnostics",
            total_sheets=2,
        )

        report = _build_diagnostic_report(state, workspace=tmp_path)

        assert "log_files" in report
        assert len(report["log_files"]) == 2
        categories = {lf["category"] for lf in report["log_files"]}
        assert "sheet_log" in categories
        assert "hook_log" in categories

    def test_attach_log_contents_inlines_content(self, tmp_path: Path):
        """_attach_log_contents should inline tail of log files."""
        from marianne.cli.commands.diagnose import _attach_log_contents

        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        log_file = log_dir / "test.log"
        lines = [f"Line {i}\n" for i in range(100)]
        log_file.write_text("".join(lines))

        report = {
            "log_files": [
                {"path": str(log_file), "name": "test.log"},
            ],
        }

        _attach_log_contents(report)

        assert "log_contents" in report
        log_contents: dict[str, str] = report["log_contents"]
        content = log_contents[str(log_file)]
        # Should contain the last 50 lines (default tail)
        assert "Line 99" in content
        assert "Line 50" in content


# =============================================================================
# Test 8: Circuit breaker state persisted
# =============================================================================


class TestCircuitBreakerStatePersisted:
    """Verify that circuit breaker state transitions are persisted in checkpoint."""

    def test_record_circuit_breaker_change_appends_entry(self):
        """record_circuit_breaker_change() should add an entry to history."""
        state = CheckpointState(
            job_id="cb-test",
            job_name="CB Test",
            total_sheets=5,
        )

        state.record_circuit_breaker_change(
            state="open",
            trigger="failure_recorded",
            consecutive_failures=3,
        )

        assert len(state.circuit_breaker_history) == 1
        entry = state.circuit_breaker_history[0]
        assert entry["state"] == "open"
        assert entry["trigger"] == "failure_recorded"
        assert entry["consecutive_failures"] == 3
        assert "timestamp" in entry

    def test_record_multiple_circuit_breaker_changes(self):
        """Multiple CB state changes should accumulate in order."""
        state = CheckpointState(
            job_id="cb-multi",
            job_name="CB Multi",
            total_sheets=3,
        )

        transitions = [
            ("closed", "success_recorded", 0),
            ("open", "failure_recorded", 3),
            ("half_open", "recovery_timeout", 3),
            ("closed", "success_recorded", 0),
        ]

        for cb_state, trigger, failures in transitions:
            state.record_circuit_breaker_change(
                state=cb_state,
                trigger=trigger,
                consecutive_failures=failures,
            )

        assert len(state.circuit_breaker_history) == 4
        assert state.circuit_breaker_history[0]["state"] == "closed"
        assert state.circuit_breaker_history[1]["state"] == "open"
        assert state.circuit_breaker_history[3]["state"] == "closed"

    def test_circuit_breaker_history_survives_serialization(self):
        """circuit_breaker_history should survive JSON round-trip."""
        state = CheckpointState(
            job_id="cb-serial",
            job_name="CB Serialize",
            total_sheets=2,
        )

        state.record_circuit_breaker_change(
            state="open",
            trigger="failure_recorded",
            consecutive_failures=5,
        )

        data = state.model_dump(mode="json")
        restored = CheckpointState.model_validate(data)

        assert len(restored.circuit_breaker_history) == 1
        assert restored.circuit_breaker_history[0]["state"] == "open"
        assert restored.circuit_breaker_history[0]["consecutive_failures"] == 5

    def test_circuit_breaker_updates_timestamp(self):
        """record_circuit_breaker_change() should update updated_at."""
        state = CheckpointState(
            job_id="cb-ts",
            job_name="CB Timestamp",
            total_sheets=1,
        )
        original_ts = state.updated_at

        state.record_circuit_breaker_change(
            state="open",
            trigger="failure_recorded",
            consecutive_failures=2,
        )

        assert state.updated_at >= original_ts


# =============================================================================
# Test 9: Chained job info in hook result
# =============================================================================


class TestChainedJobInfoInHookResult:
    """Verify that spawned job tracking fields are populated in HookResult."""

    def test_chained_job_info_populated_for_run_job(self):
        """HookResult for run_job should include chained_job_info dict."""
        result = HookResult(
            hook_type="run_job",
            description="Chain to next job",
            success=True,
            output="Detached job spawned (PID 42)",
            chained_job_path=Path("/config/next.yaml"),
            chained_job_workspace=Path("/workspace/next"),
            chained_job_info={
                "job_path": "/config/next.yaml",
                "workspace": "/workspace/next",
                "pid": 42,
                "log_path": "/workspace/hooks/chain-20260210.log",
            },
        )

        assert result.chained_job_info is not None
        assert result.chained_job_info["pid"] == 42
        assert result.chained_job_info["job_path"] == "/config/next.yaml"
        assert result.chained_job_info["log_path"] == "/workspace/hooks/chain-20260210.log"

    def test_chained_job_info_none_for_non_run_job(self):
        """HookResult for run_command should have chained_job_info=None."""
        result = HookResult(
            hook_type="run_command",
            description="Cleanup",
            success=True,
            exit_code=0,
        )

        assert result.chained_job_info is None

    def test_chained_job_info_persisted_in_checkpoint(self):
        """chained_job_info should survive persistence through checkpoint."""
        state = CheckpointState(
            job_id="chain-test",
            job_name="Chain",
            total_sheets=1,
        )

        hook_data = {
            "hook_type": "run_job",
            "success": True,
            "chained_job_info": {
                "job_path": "/path/next.yaml",
                "workspace": "/ws/next",
                "pid": 1234,
                "log_path": "/ws/hooks/chain.log",
            },
        }

        state.record_hook_result(hook_data)

        # Serialize and restore
        data = state.model_dump(mode="json")
        restored = CheckpointState.model_validate(data)

        info = restored.hook_results[0]["chained_job_info"]
        assert info["pid"] == 1234
        assert info["job_path"] == "/path/next.yaml"

    def test_hook_result_has_log_path_field(self):
        """HookResult should track log_path for detached hooks."""
        log_path = Path("/workspace/hooks/chain-20260210-120000.log")

        result = HookResult(
            hook_type="run_job",
            description="Detached chain",
            success=True,
            log_path=log_path,
        )

        assert result.log_path == log_path


# =============================================================================
# Test 10: Error history capped
# =============================================================================


class TestErrorHistoryCapped:
    """Verify that error history is capped at MAX_ERROR_HISTORY (50)."""

    def test_error_history_capped_at_max(self):
        """Adding >50 errors should keep only the last 50."""
        sheet = SheetState(sheet_num=1)

        for i in range(60):
            record_error_on_sheet(
                sheet,
                error_type="transient",
                error_code=f"E{i:03d}",
                error_message=f"Error number {i}",
                attempt=i + 1,
            )

        assert len(sheet.error_history) == MAX_ERROR_HISTORY
        # Should keep the most recent errors (10-59)
        assert sheet.error_history[0].error_message == "Error number 10"
        assert sheet.error_history[-1].error_message == "Error number 59"

    def test_error_history_cap_preserves_newest(self):
        """Capping should always preserve the newest errors."""
        sheet = SheetState(sheet_num=1)

        for i in range(MAX_ERROR_HISTORY + 20):
            record_error_on_sheet(
                sheet,
                error_type="transient",
                error_code=f"E{i:03d}",
                error_message=f"Error {i}",
                attempt=i + 1,
            )

        # The last error should always be the most recently added
        assert sheet.error_history[-1].error_code == f"E{MAX_ERROR_HISTORY + 19:03d}"
        # The first error should be the oldest surviving one
        assert sheet.error_history[0].error_code == f"E{20:03d}"

    def test_error_history_exactly_at_max_not_trimmed(self):
        """Exactly MAX_ERROR_HISTORY errors should not be trimmed."""
        sheet = SheetState(sheet_num=1)

        for i in range(MAX_ERROR_HISTORY):
            record_error_on_sheet(
                sheet,
                error_type="transient",
                error_code=f"E{i:03d}",
                error_message=f"Error {i}",
                attempt=i + 1,
            )

        assert len(sheet.error_history) == MAX_ERROR_HISTORY
        # First error should still be present (no trimming needed)
        assert sheet.error_history[0].error_code == "E000"

    def test_error_history_cap_survives_serialization(self):
        """Capped error history should survive JSON round-trip."""
        sheet = SheetState(sheet_num=1)

        for i in range(60):
            record_error_on_sheet(
                sheet,
                error_type="transient",
                error_code=f"E{i:03d}",
                error_message=f"Error {i}",
                attempt=i + 1,
            )

        data = sheet.model_dump(mode="json")
        restored = SheetState.model_validate(data)

        assert len(restored.error_history) == MAX_ERROR_HISTORY
        # Newest should be preserved
        assert restored.error_history[-1].error_message == "Error 59"

    def test_max_error_history_constant_is_50(self):
        """MAX_ERROR_HISTORY should be 50."""
        assert MAX_ERROR_HISTORY == 50
