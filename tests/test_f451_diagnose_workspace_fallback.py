"""Tests for F-451: Diagnose can't find completed jobs that status can find.

F-451: When the conductor says "job not found" (e.g., after restart),
diagnose should fall back to workspace filesystem search if a workspace
is provided via -w, rather than immediately exiting with "Score not found".
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marianne.core.checkpoint import CheckpointState


class TestDiagnoseWorkspaceFallback:
    """When conductor can't find a job, diagnose falls back to filesystem."""

    @pytest.mark.asyncio
    async def test_conductor_not_found_falls_back_to_workspace(
        self, tmp_path: Path,
    ) -> None:
        """JobSubmissionError with -w flag falls back to filesystem instead of exiting."""
        from marianne.daemon.exceptions import JobSubmissionError

        workspace = tmp_path / "ws"
        workspace.mkdir()

        mock_state = MagicMock(spec=CheckpointState)
        mock_state.job_id = "test-job"
        mock_state.status = "COMPLETED"
        mock_state.sheets = {}
        mock_backend = AsyncMock()
        mock_backend.get_execution_history_count = AsyncMock(return_value=0)
        mock_find = AsyncMock(return_value=(mock_state, mock_backend))

        with (
            patch(
                "marianne.daemon.detect.try_daemon_route",
                AsyncMock(side_effect=JobSubmissionError("Not found")),
            ),
            patch(
                "marianne.cli.helpers._find_job_state_direct",
                mock_find,
            ),
            patch(
                "marianne.cli.commands.diagnose._build_diagnostic_report",
                return_value={"job_id": "test-job", "status": "COMPLETED"},
            ),
            patch(
                "marianne.cli.commands.diagnose._display_diagnostic_report",
            ),
        ):
            from marianne.cli.commands.diagnose import _diagnose_job

            # Should NOT raise typer.Exit — falls back to workspace
            await _diagnose_job(
                "test-job",
                workspace=workspace,
                json_output=False,
            )

        # Verify filesystem fallback was called with correct args
        mock_find.assert_called_once_with(
            "test-job", workspace, json_output=False,
        )

    @pytest.mark.asyncio
    async def test_conductor_not_found_no_workspace_still_errors(self) -> None:
        """JobSubmissionError without -w flag still exits with error."""
        import typer

        from marianne.daemon.exceptions import JobSubmissionError

        with (
            patch(
                "marianne.daemon.detect.try_daemon_route",
                AsyncMock(side_effect=JobSubmissionError("Not found")),
            ),
            patch("marianne.cli.commands.diagnose.output_error"),
        ):
            from marianne.cli.commands.diagnose import _diagnose_job

            with pytest.raises(typer.Exit):
                await _diagnose_job(
                    "test-job",
                    workspace=None,
                    json_output=False,
                )

    @pytest.mark.asyncio
    async def test_workspace_fallback_not_found_also_exits(
        self, tmp_path: Path,
    ) -> None:
        """When filesystem fallback also fails, exits with error."""
        import typer

        from marianne.daemon.exceptions import JobSubmissionError

        workspace = tmp_path / "ws"
        workspace.mkdir()

        with (
            patch(
                "marianne.daemon.detect.try_daemon_route",
                AsyncMock(side_effect=JobSubmissionError("Not found")),
            ),
            patch(
                "marianne.cli.helpers._find_job_state_direct",
                AsyncMock(side_effect=typer.Exit(1)),
            ),
        ):
            from marianne.cli.commands.diagnose import _diagnose_job

            with pytest.raises(typer.Exit):
                await _diagnose_job(
                    "test-job",
                    workspace=workspace,
                    json_output=False,
                )

    @pytest.mark.asyncio
    async def test_no_workspace_hint_mentions_w_flag(self) -> None:
        """Error hints should mention -w flag when workspace not provided."""
        import typer

        from marianne.daemon.exceptions import JobSubmissionError

        mock_output_error = MagicMock()

        with (
            patch(
                "marianne.daemon.detect.try_daemon_route",
                AsyncMock(side_effect=JobSubmissionError("Not found")),
            ),
            patch(
                "marianne.cli.commands.diagnose.output_error",
                mock_output_error,
            ),
        ):
            from marianne.cli.commands.diagnose import _diagnose_job

            with pytest.raises(typer.Exit):
                await _diagnose_job(
                    "test-job",
                    workspace=None,
                    json_output=False,
                )

        # Verify hint mentions -w flag
        call_args = mock_output_error.call_args
        hints = call_args.kwargs.get("hints", call_args[1].get("hints", []))
        hint_text = " ".join(hints)
        assert "-w" in hint_text, f"Hints should mention -w flag: {hints}"
