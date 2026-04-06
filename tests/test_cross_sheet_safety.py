"""Tests for cross-sheet context safety — credential redaction and skipped upstream.

F-250: capture_files content must be credential-redacted before injection
into prompt context. Without this, an agent that writes a file containing
an API key would have that key forwarded to the next sheet's prompt.

F-251: Baton cross-sheet context must inject [SKIPPED] placeholders and
populate skipped_upstream for skipped upstream sheets, matching the legacy
runner's behavior (#120).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest


# =============================================================================
# F-250: Credential Redaction in capture_files
# =============================================================================


class TestCaptureFilesCredentialRedaction:
    """capture_files content must be scanned and redacted for credentials."""

    def test_legacy_runner_redacts_capture_files(self, tmp_path: Path) -> None:
        """Legacy runner _capture_cross_sheet_files redacts credentials."""
        from marianne.prompts.templating import SheetContext

        # Write a workspace file containing an API key
        secret_file = tmp_path / "config.env"
        secret_file.write_text(
            "ANTHROPIC_API_KEY=sk-ant-api03-FAKEKEYFAKEKEYFAKEKEY12345678"
        )

        mixin = _make_context_mixin(workspace=tmp_path)

        from marianne.core.config.workspace import CrossSheetConfig

        cross_sheet = CrossSheetConfig(
            capture_files=[str(secret_file)],
            max_output_chars=10000,
        )

        context = SheetContext(
            sheet_num=2,
            total_sheets=3,
            start_item=2,
            end_item=2,
            workspace=tmp_path,
        )

        state = _make_mock_state(started_at_ts=0.0)
        mixin._capture_cross_sheet_files(context, state, 2, cross_sheet)

        assert str(secret_file) in context.previous_files
        content = context.previous_files[str(secret_file)]

        # Credential should be redacted
        assert "sk-ant-api03-FAKEKEYFAKEKEYFAKEKEY12345678" not in content
        assert "[REDACTED" in content

    def test_legacy_runner_redacts_openai_key_in_files(
        self, tmp_path: Path
    ) -> None:
        """OpenAI keys in captured files are redacted."""
        from marianne.prompts.templating import SheetContext

        secret_file = tmp_path / "secrets.txt"
        secret_file.write_text(
            "api_key = sk-proj-ABCDEFGHIJKLMNOPQRSTuvwxyz1234567890abc"
        )

        mixin = _make_context_mixin(workspace=tmp_path)

        from marianne.core.config.workspace import CrossSheetConfig

        cross_sheet = CrossSheetConfig(
            capture_files=[str(secret_file)],
            max_output_chars=10000,
        )

        context = SheetContext(
            sheet_num=2,
            total_sheets=3,
            start_item=2,
            end_item=2,
            workspace=tmp_path,
        )
        state = _make_mock_state(started_at_ts=0.0)
        mixin._capture_cross_sheet_files(context, state, 2, cross_sheet)

        content = context.previous_files[str(secret_file)]
        assert "sk-proj-ABCDEFGHIJKLMNOPQRST" not in content
        assert "[REDACTED" in content

    def test_baton_adapter_redacts_capture_files(self, tmp_path: Path) -> None:
        """Baton adapter _collect_cross_sheet_context redacts credentials."""
        from marianne.core.config.workspace import CrossSheetConfig

        secret_file = tmp_path / "leaked.txt"
        secret_file.write_text(
            "token: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef1234"
        )

        adapter = _make_baton_adapter()

        job_id = "test-job"
        adapter._job_cross_sheet[job_id] = CrossSheetConfig(
            capture_files=[str(secret_file)],
            max_output_chars=10000,
        )

        mock_sheet = MagicMock()
        mock_sheet.workspace = tmp_path
        adapter._job_sheets[job_id] = {1: mock_sheet, 2: mock_sheet}
        adapter._baton._jobs = {job_id: MagicMock(sheets={})}

        prev_outputs, prev_files = adapter._collect_cross_sheet_context(
            job_id, 2
        )

        assert str(secret_file) in prev_files
        content = prev_files[str(secret_file)]
        assert "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef1234" not in content
        assert "[REDACTED" in content

    def test_non_credential_content_preserved(self, tmp_path: Path) -> None:
        """Normal file content (no credentials) is preserved unchanged."""
        from marianne.core.config.workspace import CrossSheetConfig

        normal_file = tmp_path / "output.md"
        normal_file.write_text("# Analysis Results\n\nThe data shows improvement.")

        adapter = _make_baton_adapter()
        job_id = "test-job"
        adapter._job_cross_sheet[job_id] = CrossSheetConfig(
            capture_files=[str(normal_file)],
            max_output_chars=10000,
        )
        mock_sheet = MagicMock()
        mock_sheet.workspace = tmp_path
        adapter._job_sheets[job_id] = {1: mock_sheet, 2: mock_sheet}
        adapter._baton._jobs = {job_id: MagicMock(sheets={})}

        _, prev_files = adapter._collect_cross_sheet_context(job_id, 2)

        assert (
            prev_files[str(normal_file)]
            == "# Analysis Results\n\nThe data shows improvement."
        )

    def test_truncation_happens_after_redaction(self, tmp_path: Path) -> None:
        """File content is redacted first, then truncated."""
        from marianne.core.config.workspace import CrossSheetConfig

        padding = "x" * 50
        credential = "sk-ant-api03-FAKEKEYFAKEKEYFAKEKEY12345678"
        secret_file = tmp_path / "long.txt"
        secret_file.write_text(padding + credential)

        adapter = _make_baton_adapter()
        job_id = "test-job"
        adapter._job_cross_sheet[job_id] = CrossSheetConfig(
            capture_files=[str(secret_file)],
            max_output_chars=200,
        )
        mock_sheet = MagicMock()
        mock_sheet.workspace = tmp_path
        adapter._job_sheets[job_id] = {1: mock_sheet, 2: mock_sheet}
        adapter._baton._jobs = {job_id: MagicMock(sheets={})}

        _, prev_files = adapter._collect_cross_sheet_context(job_id, 2)

        content = prev_files[str(secret_file)]
        assert credential not in content

    def test_bearer_token_in_files_redacted(self, tmp_path: Path) -> None:
        """Bearer tokens in captured workspace files are redacted."""
        from marianne.core.config.workspace import CrossSheetConfig

        config_file = tmp_path / "api-config.json"
        config_file.write_text(
            '{"Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"}'
        )

        adapter = _make_baton_adapter()
        job_id = "test-job"
        adapter._job_cross_sheet[job_id] = CrossSheetConfig(
            capture_files=[str(config_file)],
            max_output_chars=10000,
        )
        mock_sheet = MagicMock()
        mock_sheet.workspace = tmp_path
        adapter._job_sheets[job_id] = {1: mock_sheet, 2: mock_sheet}
        adapter._baton._jobs = {job_id: MagicMock(sheets={})}

        _, prev_files = adapter._collect_cross_sheet_context(job_id, 2)

        content = prev_files[str(config_file)]
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in content
        assert "[REDACTED" in content

    def test_aws_key_in_files_redacted(self, tmp_path: Path) -> None:
        """AWS access keys in captured files are redacted."""
        from marianne.core.config.workspace import CrossSheetConfig

        env_file = tmp_path / ".env"
        env_file.write_text("AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE")

        adapter = _make_baton_adapter()
        job_id = "test-job"
        adapter._job_cross_sheet[job_id] = CrossSheetConfig(
            capture_files=[str(env_file)],
            max_output_chars=10000,
        )
        mock_sheet = MagicMock()
        mock_sheet.workspace = tmp_path
        adapter._job_sheets[job_id] = {1: mock_sheet, 2: mock_sheet}
        adapter._baton._jobs = {job_id: MagicMock(sheets={})}

        _, prev_files = adapter._collect_cross_sheet_context(job_id, 2)

        content = prev_files[str(env_file)]
        assert "AKIAIOSFODNN7EXAMPLE" not in content
        assert "[REDACTED" in content


# =============================================================================
# F-251: Baton [SKIPPED] Placeholder + skipped_upstream
# =============================================================================


class TestBatonSkippedUpstream:
    """Baton cross-sheet context must handle skipped upstream sheets."""

    def test_skipped_sheets_get_placeholder(self) -> None:
        """Skipped upstream sheets should inject [SKIPPED] in previous_outputs."""
        from marianne.core.config.workspace import CrossSheetConfig
        from marianne.daemon.baton.state import BatonSheetStatus, SheetExecutionState

        adapter = _make_baton_adapter()
        job_id = "test-job"
        adapter._job_cross_sheet[job_id] = CrossSheetConfig(
            auto_capture_stdout=True,
            lookback_sheets=0,
            max_output_chars=2000,
        )

        mock_sheet = MagicMock()
        mock_sheet.workspace = Path("/tmp/test")
        adapter._job_sheets[job_id] = {
            1: mock_sheet,
            2: mock_sheet,
            3: mock_sheet,
            4: mock_sheet,
        }

        # Sheet 1: completed, sheet 2: skipped, sheet 3: completed
        completed_1 = SheetExecutionState(
            sheet_num=1, instrument_name="test", status=BatonSheetStatus.COMPLETED
        )
        completed_1.attempt_results = [
            _make_attempt_result(stdout_tail="output from sheet 1"),
        ]
        skipped_2 = SheetExecutionState(
            sheet_num=2, instrument_name="test", status=BatonSheetStatus.SKIPPED
        )
        completed_3 = SheetExecutionState(
            sheet_num=3, instrument_name="test", status=BatonSheetStatus.COMPLETED
        )
        completed_3.attempt_results = [
            _make_attempt_result(stdout_tail="output from sheet 3"),
        ]

        mock_job_state = MagicMock()
        mock_job_state.sheets = {
            1: completed_1,
            2: skipped_2,
            3: completed_3,
        }
        adapter._baton._jobs = {job_id: mock_job_state}

        prev_outputs, _ = adapter._collect_cross_sheet_context(job_id, 4)

        assert prev_outputs[1] == "output from sheet 1"
        assert prev_outputs[2] == "[SKIPPED]"
        assert prev_outputs[3] == "output from sheet 3"

    def test_only_skipped_status_gets_placeholder(self) -> None:
        """PENDING/FAILED sheets should NOT get [SKIPPED] placeholder."""
        from marianne.core.config.workspace import CrossSheetConfig
        from marianne.daemon.baton.state import BatonSheetStatus, SheetExecutionState

        adapter = _make_baton_adapter()
        job_id = "test-job"
        adapter._job_cross_sheet[job_id] = CrossSheetConfig(
            auto_capture_stdout=True,
            lookback_sheets=0,
            max_output_chars=2000,
        )

        mock_sheet = MagicMock()
        mock_sheet.workspace = Path("/tmp/test")
        adapter._job_sheets[job_id] = {
            1: mock_sheet,
            2: mock_sheet,
            3: mock_sheet,
        }

        # Sheet 1: FAILED (not completed, not skipped)
        failed_1 = SheetExecutionState(
            sheet_num=1, instrument_name="test", status=BatonSheetStatus.FAILED
        )
        mock_job_state = MagicMock()
        mock_job_state.sheets = {1: failed_1}
        adapter._baton._jobs = {job_id: mock_job_state}

        prev_outputs, _ = adapter._collect_cross_sheet_context(job_id, 3)

        # FAILED should NOT produce [SKIPPED] — it should be absent
        assert 1 not in prev_outputs

    def test_skipped_upstream_with_lookback(self) -> None:
        """[SKIPPED] placeholder respects lookback_sheets window."""
        from marianne.core.config.workspace import CrossSheetConfig
        from marianne.daemon.baton.state import BatonSheetStatus, SheetExecutionState

        adapter = _make_baton_adapter()
        job_id = "test-job"
        adapter._job_cross_sheet[job_id] = CrossSheetConfig(
            auto_capture_stdout=True,
            lookback_sheets=2,  # Only look at last 2 sheets
            max_output_chars=2000,
        )

        mock_sheet = MagicMock()
        mock_sheet.workspace = Path("/tmp/test")
        adapter._job_sheets[job_id] = {
            n: mock_sheet for n in range(1, 6)
        }

        # Sheets 1 and 3 skipped, but lookback=2 from sheet 5 means only 3,4
        skipped_1 = SheetExecutionState(
            sheet_num=1, instrument_name="test", status=BatonSheetStatus.SKIPPED
        )
        completed_2 = SheetExecutionState(
            sheet_num=2, instrument_name="test", status=BatonSheetStatus.COMPLETED
        )
        skipped_3 = SheetExecutionState(
            sheet_num=3, instrument_name="test", status=BatonSheetStatus.SKIPPED
        )
        completed_4 = SheetExecutionState(
            sheet_num=4, instrument_name="test", status=BatonSheetStatus.COMPLETED
        )
        completed_4.attempt_results = [
            _make_attempt_result(stdout_tail="s4"),
        ]

        mock_job_state = MagicMock()
        mock_job_state.sheets = {
            1: skipped_1,
            2: completed_2,
            3: skipped_3,
            4: completed_4,
        }
        adapter._baton._jobs = {job_id: mock_job_state}

        prev_outputs, _ = adapter._collect_cross_sheet_context(job_id, 5)

        # Sheet 1 is outside lookback window (5-2=3, range starts at 3)
        assert 1 not in prev_outputs
        # Sheet 3 is in window and skipped
        assert prev_outputs.get(3) == "[SKIPPED]"
        # Sheet 4 is in window and completed
        assert prev_outputs.get(4) == "s4"


# =============================================================================
# Helpers
# =============================================================================


def _make_context_mixin(workspace: Path) -> Any:
    """Create a minimal ContextBuildingMixin instance for testing."""
    from marianne.execution.runner.context import ContextBuildingMixin

    class FakeRunner(ContextBuildingMixin):
        def __init__(self, ws: Path) -> None:
            import logging

            self._logger = logging.getLogger("test")
            self.config = MagicMock()
            self.config.workspace = ws

    return FakeRunner(workspace)


def _make_mock_state(started_at_ts: float = 0.0) -> MagicMock:
    """Create a mock CheckpointState with a started_at timestamp."""
    from datetime import datetime, timezone

    state = MagicMock()
    state.started_at = datetime.fromtimestamp(started_at_ts, tz=timezone.utc)
    state.sheets = {}
    return state


def _make_baton_adapter() -> Any:
    """Create a minimal BatonAdapter for testing."""
    from marianne.daemon.baton.adapter import BatonAdapter

    adapter = BatonAdapter(
        event_bus=MagicMock(),
    )
    return adapter


def _make_attempt_result(
    stdout_tail: str = "",
    execution_success: bool = True,
) -> Any:
    """Create a SheetAttemptResult for testing."""
    from marianne.daemon.baton.events import SheetAttemptResult

    return SheetAttemptResult(
        job_id="test-job",
        sheet_num=1,
        instrument_name="test",
        attempt=1,
        execution_success=execution_success,
        stdout_tail=stdout_tail,
    )
